import json
from pathlib import Path
from types import MethodType
from typing import cast

import pytest
from pydantic import BaseModel
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.commands.research_history import ResearchHistoryHandlers
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.research_history import HistoricalResultView, HistoryDetail, HistoryPage
from thoth.protocol.jsonrpc import JsonRpcResponse
from thoth.protocol.registry import MethodRegistry


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _items(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _rpc_model[T: BaseModel](response: JsonRpcResponse, codec: type[T]) -> T:
    assert response.error is None, response.error
    assert response.result is not None
    return codec.model_validate_json(json.dumps(_record(response.result["value"])), strict=True)


def _history_host(runtime: AppRuntime) -> ResearchHistoryHandlers:
    registry: object = vars(runtime.bus).get("_registry")
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve("revision/timeline/read")
    assert isinstance(bound, MethodType)
    owner = bound.__self__
    assert isinstance(owner, ResearchHistoryHandlers)
    return owner

async def test_history_query_is_read_only_and_scope_mismatch_is_rejected(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        before = snapshot(runtime.ledger.engine)
        page = _rpc_model(
            await runtime.bus.query(
                request(
                    "revision/timeline/read",
                    "history",
                    {
                        "project_id": "p",
                        "scope": {"project_id": "p"},
                        "limit": 2,
                    },
                )
            ),
            HistoryPage,
        )
        assert page.contract_version == 2
        assert page.capability.apply_ready is False
        assert len(page.actor_scope_digest) == 64
        denied = await runtime.bus.query(
            request(
                "revision/timeline/read",
                "bad-scope",
                {
                    "project_id": "p",
                    "scope": {"project_id": "elsewhere"},
                },
            )
        )
        assert denied.error is not None
        assert denied.error.data["reason_code"] == "HISTORY_SCOPE_MISMATCH"
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


async def test_branch_read_and_watermark_exclude_later_insert_and_hidden_page_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_restore_preview_contract import prepared, revise

    runtime, _model, _accepted, candidates = await prepared(tmp_path)
    try:
        revision, snap = candidates["hypothesis.v1"]
        revise(runtime, revision, snap, "statement", "Current B")
        query: dict[str, object] = {"project_id": "p", "scope": {"project_id": "p"}, "limit": 1}
        initial = _rpc_model(
            await runtime.bus.query(request("revision/timeline/read", "page-one", query)),
            HistoryPage,
        )
        branch = revise(runtime, revision, snap, "statement", "Independent C")
        observed = list(initial.items)
        cursor = initial.next_cursor
        while cursor:
            page = _rpc_model(
                await runtime.bus.query(
                    request("revision/timeline/read", "next", {**query, "cursor": cursor})
                ),
                HistoryPage,
            )
            observed.extend(page.items)
            cursor = page.next_cursor
        ids = [item.record_ref.revision_digest for item in observed]
        assert len(ids) == len(set(ids)) and branch.revision_digest not in ids
        refreshed = _rpc_model(
            await runtime.bus.query(
                request("revision/timeline/read", "refresh", {**query, "limit": 50})
            ),
            HistoryPage,
        )
        item = next(
            item
            for item in refreshed.items
            if item.record_ref.revision_digest == branch.revision_digest
        )
        assert item.head_membership == "BRANCH"
        detail = _rpc_model(
            await runtime.bus.query(
                request(
                    "revision/timeline/item/read",
                    "branch",
                    {
                        "project_id": "p",
                        "scope": {"project_id": "p"},
                        "record_ref": item.record_ref.model_dump(mode="json"),
                    },
                )
            ),
            HistoryDetail,
        )
        assert detail.content is not None
        assert detail.content["statement"] == "Independent C"
        assert detail.current_head_digest != branch.revision_digest
        service = _history_host(runtime).history
        service.scan_limit = 2
        def deny_revision(*args: object) -> bool:
            return False

        monkeypatch.setattr(service.projection.access, "may_read_revision", deny_revision)
        before = snapshot(runtime.ledger.engine)
        hidden = _rpc_model(
            await runtime.bus.query(request("revision/timeline/read", "hidden", query)),
            HistoryPage,
        )
        assert (
            hidden.items == ()
            and hidden.next_cursor
            and hidden.coverage.scan == "LIMITED"
        )
        advanced = _rpc_model(
            await runtime.bus.query(
                request(
                    "revision/timeline/read",
                    "hidden-next",
                    {**query, "cursor": hidden.next_cursor},
                )
            ),
            HistoryPage,
        )
        assert advanced.next_cursor != hidden.next_cursor
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


async def test_exact_old_result_does_not_read_the_new_request(tmp_path: Path) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    try:
        first = _record(value(
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
        ))
        await runtime.bus.drain()
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "second",
                    {
                        "project_id": "p",
                        "thread_id": _text(first["thread_id"]),
                        "instruction": "Second question",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        calls = len(model.calls)
        conversation = _record(value(
            await runtime.bus.query(
                request(
                    "thread/activity/list",
                    "conversation",
                    {
                        "project_id": "p",
                        "thread_id": _text(first["thread_id"]),
                    },
                )
            )
        ))["conversation"]
        conversation = _record(conversation)
        first_turn = _record(_items(conversation["turns"])[0])
        old_request_digest = _text(first_turn["request_revision_digest"])
        before = snapshot(runtime.ledger.engine)
        old = _rpc_model(
            await runtime.bus.query(
                request(
                    "thread/result/read",
                    "old",
                    {
                        "project_id": "p",
                        "thread_id": _text(first["thread_id"]),
                        "request_revision_digest": old_request_digest,
                    },
                )
            ),
            HistoricalResultView,
        )
        assert old.request["effective_question"] == "First question"
        assert old.operation_id == first["operation_id"]
        assert old.manifest is not None
        assert (
            _record(old.manifest["request_ref"])["revision_digest"]
            == old_request_digest
        )
        assert len(model.calls) == calls
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()
