import asyncio
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.services import resource_scope_read_context
from thoth.application.services.conversation_inputs import conversation_inputs
from thoth.application.services.research_boundary import RequestBoundary
from thoth.application.services.resource_scope_read_context import ReadLookupMemo
from thoth.domain.account_usage import AccountQuotaSnapshot
from thoth.domain.enums import EntityType
from thoth.domain.research_request import ResearchBudget, ThreadRequestRevision


def _memo_active() -> bool:
    variable: object = vars(resource_scope_read_context).get("_READ_LOOKUPS")
    assert isinstance(variable, ContextVar)
    memo: object = cast(ContextVar[ReadLookupMemo | None], variable).get()
    assert memo is None or isinstance(memo, ReadLookupMemo)
    return memo is not None and memo.active


async def test_two_questions_read_their_own_input_and_operation_result_without_writes(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {
                        "project_id": "p",
                        "problem": "First question",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        second = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "second",
                    {
                        "project_id": "p",
                        "thread_id": first["thread_id"],
                        "instruction": "Second question",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        before = snapshot(runtime.ledger.engine)
        page = value(
            await runtime.bus.query(
                request(
                    "thread/activity/list",
                    "history",
                    {
                        "project_id": "p",
                        "thread_id": first["thread_id"],
                    },
                )
            )
        )["conversation"]
        assert [turn["text"] for turn in page["turns"]] == ["First question", "Second question"]
        assert [turn["operation_id"] for turn in page["turns"]] == [
            first["operation_id"],
            second["operation_id"],
        ]
        assert len({turn["request_revision_digest"] for turn in page["turns"]}) == 2
        older = value(
            await runtime.bus.query(
                request(
                    "thread/activity/list",
                    "older",
                    {
                        "project_id": "p",
                        "thread_id": first["thread_id"],
                        "conversation_before_epoch": 2,
                    },
                )
            )
        )["conversation"]
        assert [turn["text"] for turn in older["turns"]] == ["First question"]
        for turn in page["turns"]:
            result = value(
                await runtime.bus.query(
                    request(
                        "operation/result/read",
                        "read",
                        {
                            "project_id": "p",
                            "operation_id": turn["operation_id"],
                        },
                    )
                )
            )
            assert result["result"]["request_epoch"] == turn["request_epoch"]
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        host = research_host(runtime)
        assert isinstance(host, ResearchThreadHandlers)

        def deny(*_: object) -> bool:
            return False

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(host.access, "may_read_revision", deny)
            assert (
                conversation_inputs(host.records, host.access, "p", str(first["thread_id"]), None)[
                    "turns"
                ]
                == []
            )
    finally:
        runtime.close()


async def test_legacy_token_call_and_time_reservations_are_observational(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Question",
                        "contract_version": 2,
                    },
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 10)
        host = research_host(runtime)
        assert isinstance(host, ResearchThreadHandlers)
        current = host.records.read("p", EntityType.THREAD, f"request:{accepted['thread_id']}")
        operation = runtime.bus.read_operation(str(accepted["operation_id"]))
        assert current is not None and operation is not None
        boundary = RequestBoundary(
            host.records,
            host.projects,
            host.threads,
            host.governance,
            host.operations,
            operation,
            ThreadRequestRevision.model_validate(current[1]),
        )
        key = f"budget:{accepted['thread_id']}"
        budget = ResearchBudget(
            started_at=host.records.clock.now().isoformat(),
            max_reserved_tokens=400_000,
            reserved_tokens=500_000,
        )
        host.records.journal("p", key, budget)
        boundary.reserve(100, 100)
        updated = host.records.journal_read("p", key, ResearchBudget)
        assert updated is not None and updated.reserved_tokens == 500_200 and updated.calls == 1
        host.records.journal("p", key, updated.model_copy(update={"calls": 24}))
        boundary.reserve(1)
        after_calls = host.records.journal_read("p", key, ResearchBudget)
        assert after_calls is not None and after_calls.calls == 25
        host.records.journal(
            "p",
            key,
            updated.model_copy(
                update={
                    "started_at": (host.records.clock.now() - timedelta(seconds=901)).isoformat()
                }
            ),
        )
        boundary.reserve(1)
        assert boundary.call_timeout() is None
    finally:
        model.release.set()
        await runtime.bus.drain()
        runtime.close()


async def test_thread_read_account_quota_refresh_runs_outside_scope_read_memo(
    tmp_path: Path,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "quota-start",
                    {
                        "project_id": "p",
                        "problem": "Quota refresh boundary",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        host = research_host(runtime)
        assert isinstance(host, ResearchThreadHandlers)
        refresh_memo_active: list[bool] = []

        class ObservedQuota:
            def refresh(self) -> AccountQuotaSnapshot:
                refresh_memo_active.append(_memo_active())
                return AccountQuotaSnapshot(
                    state="OBSERVED",
                    provider="codex-oauth",
                )

        host.provider_usage.adapter = ObservedQuota()
        host.provider_usage.adapters = {}
        result = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "quota-read",
                    {
                        "project_id": "p",
                        "thread_id": accepted["thread_id"],
                        "contract_version": 2,
                        "refresh_account_quota": True,
                    },
                )
            )
        )
        assert refresh_memo_active == [False]
        assert result["usage"]["account_quota"]["state"] == "OBSERVED"
    finally:
        runtime.close()
