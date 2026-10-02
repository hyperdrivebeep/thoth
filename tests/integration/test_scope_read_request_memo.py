"""Inside one query the read-approval memo gives the same verdicts as no memo, with far fewer lookups."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_followup_read import (
    _followup_handlers,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.services.historical_access_verification import historical_query_verification
from thoth.application.services.resource_scope_read_context import scope_read_transaction
from thoth.domain.resource_scope import resource_use_scope


@pytest.mark.asyncio
async def test_the_request_memo_gives_the_same_verdicts_and_reads_the_scope_store_less(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=True)
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "s",
                    {
                        "project_id": "p",
                        "problem": "LAB-42 지연 조건을 요약해줘",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        status = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "r",
                    {"project_id": "p", "thread_id": started["thread_id"], "view": "FULL"},
                )
            )
        )
        refs: list[str] = [
            f"revision:{item['revision_digest']}"
            for item in status["current_result"]["record_refs"]
        ]
        refs += [
            f"span:{ref.removeprefix('span:')}" for ref in status["current_result"]["source_refs"]
        ]
        assert len(refs) >= 3
        access: Any = _followup_handlers(runtime).reviews.access
        reads = {"count": 0}
        original = access._store.read  # pyright: ignore[reportPrivateUsage]

        def counting(*args: Any, **kwargs: Any) -> Any:
            reads["count"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(access._store, "read", counting)  # pyright: ignore[reportPrivateUsage]
        unknown = "revision:" + "0" * 64

        def verdicts() -> list[bool]:
            return [access.may_read("p", ref) for ref in (unknown, *refs, unknown, *refs)]

        with historical_query_verification(), resource_use_scope("p"):
            reads["count"] = 0
            without_memo = verdicts()
            plain_reads = reads["count"]
            with scope_read_transaction():
                reads["count"] = 0
                first = verdicts()
                first_reads = reads["count"]
                reads["count"] = 0
                second = verdicts()
                second_reads = reads["count"]
        assert without_memo == first == second
        assert False in without_memo and True in without_memo
        assert (
            without_memo[0] is False and without_memo[len(refs) + 1] is False
        )  # denials stay denials
        assert first_reads < plain_reads
        assert second_reads <= len(refs) + 2  # only the denied lookups are repeated
    finally:
        runtime.close()
