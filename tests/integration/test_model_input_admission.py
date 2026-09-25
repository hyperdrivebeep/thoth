"""Normal request ownership with local fake transport; never calls a provider."""

import asyncio
import hashlib
import inspect
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.services.connector_io import connector_io
from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.research_boundary import RequestBoundary
from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.enums import EntityType
from thoth.domain.model_dispatch import ModelCallContext, ModelDispatchRecord, OAuthSession
from thoth.domain.research_execution import (
    ResearchWork,
    model_call,
    research_work,
    reserve_model_dispatch,
)
from thoth.domain.research_request import ResearchBudget, ThreadRequestRevision
from thoth.ports.model import ModelExecutionHold


class Session:
    def read(self) -> OAuthSession:
        return OAuthSession("test-only", "test-only", "selected-model", "xhigh")


async def test_large_model_dispatch_repair_and_generic_io_keep_the_same_attempt_budget(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model, source=False)
    token = call_token = None
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "admit",
                    {
                        "project_id": "p",
                        "problem": "An independent input question",
                        "contract_version": 2,
                    },
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 10)
        handler = runtime.bus._registry.resolve("thread/read")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        host = handler.__self__
        current = host.records.read("p", EntityType.THREAD, f"request:{accepted['thread_id']}")
        operation = host.operations.read(accepted["operation_id"])
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
        original = host.records.journal_read("p", key, ResearchBudget)
        assert original is not None and original.max_prompt_bytes == 180_000
        token = research_work.set(ResearchWork(current[0], "Independent question", boundary))
        call_token = model_call.set(ModelCallContext("admission-fixture"))
        received: list[bytes] = []

        def reply(req: httpx.Request) -> httpx.Response:
            received.append(req.content)
            event = {
                "type": "response.completed",
                "response": {
                    "id": "fixture",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"ok":true}'}],
                        },
                    ],
                },
            }
            return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\n\n")

        executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(reply))
        schema: dict[str, object] = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        initial = executor.prepare("", schema, output_tokens=6000, timeout_seconds=900)
        prepared = executor.prepare(
            "x" * (180_702 - len(initial.payload)), schema, output_tokens=6000, timeout_seconds=900
        )
        assert len(prepared.payload) == 180_702
        first = reserve_model_dispatch(prepared.payload, 6000, prepared.capability)
        await executor.dispatch(prepared)
        record = host.records.journal_read("p", first, ModelDispatchRecord)
        assert record is not None and record.payload_bytes == 180_702
        assert record.payload_digest == hashlib.sha256(prepared.payload).hexdigest()
        assert received == [prepared.payload]
        boundary.reserve_dispatch(first, prepared.payload, 6000, prepared.capability)
        budget = host.records.journal_read("p", key, ResearchBudget)
        assert budget is not None and budget.calls == original.calls + 1
        assert (
            budget.started_at == original.started_at and budget.max_seconds == original.max_seconds
        )
        assert budget.reserved_tokens == original.reserved_tokens + 180_702 + 6000
        with pytest.raises(ModelExecutionHold, match="DISPATCH_IDENTITY_CONFLICT"):
            boundary.reserve_dispatch(first, b"changed", 6000, prepared.capability)
        with pytest.raises(ModelExecutionHold, match="SERIALIZED_MODEL_INPUT_LIMIT"):
            boundary.reserve(180_702)
        assert host.records.journal_read("p", key, ResearchBudget) == budget

        connector = ConnectorAccessRequest(
            actor_id="fixture",
            project_id="p",
            connector_id="fixture",
            selector={"query": "x" * 180_702},
            policy_id="fixture",
            policy_revision=1,
            policy_digest="a" * 64,
        )
        connector_calls: list[str] = []
        controls = Mock(spec=ControlRecordService)

        async def fetch() -> str:
            connector_calls.append("FETCH")
            return "unexpected"

        with pytest.raises(ModelExecutionHold, match="SERIALIZED_MODEL_INPUT_LIMIT"):
            await connector_io(connector, "FETCH", fetch, controls)
        assert connector_calls == [] and controls.mock_calls == []
        assert host.records.journal_read("p", key, ResearchBudget) == budget

        journal = host.records.journal

        def fail_record(
            project: str, record_key: str, entry: BaseModel, state: str = "RUNNING"
        ) -> None:
            if record_key == "atomic-failure":
                raise RuntimeError("INJECTED_DISPATCH_RECORD_FAILURE")
            journal(project, record_key, entry, state)

        with (
            patch.object(host.records, "journal", side_effect=fail_record),
            pytest.raises(RuntimeError, match="INJECTED_DISPATCH_RECORD_FAILURE"),
        ):
            boundary.reserve_dispatch("atomic-failure", prepared.payload, 6000, prepared.capability)
        assert host.records.journal_read("p", key, ResearchBudget) == budget
        assert host.records.journal_read("p", "atomic-failure", ModelDispatchRecord) is None

        repair = reserve_model_dispatch(prepared.payload, 6000, prepared.capability)
        assert repair != first
        await executor.dispatch(prepared)
        boundary.reserve(100)  # Existing connector/non-model reservation path.
        updated = host.records.journal_read("p", key, ResearchBudget)
        assert updated is not None and updated.calls == original.calls + 3
        assert received == [prepared.payload, prepared.payload]
        assert updated.started_at == original.started_at

        host.records.journal("p", key, updated.model_copy(update={"calls": 24, "max_calls": 24}))
        reserve_model_dispatch(prepared.payload, 6000, prepared.capability)
        after_legacy_limit = host.records.journal_read("p", key, ResearchBudget)
        assert after_legacy_limit is not None and after_legacy_limit.calls == 25
        host.records.journal(
            "p",
            key,
            updated.model_copy(
                update={
                    "started_at": (host.records.clock.now() - timedelta(seconds=901)).isoformat(),
                    "max_seconds": 900,
                }
            ),
        )
        reserve_model_dispatch(prepared.payload, 6000, prepared.capability)
        assert boundary.call_timeout() is None
        boundary.record_usage(first, 100, 12, 7, "COMPLETED", "fixture")
        usage = boundary.usage_observation()
        assert usage["input_tokens"] == 12 and usage["output_tokens"] == 7
        assert usage["total_tokens"] == 19 and usage["state"] == "PARTIAL"
        assert usage["total_time_limit_enforced"] is False
        assert usage["total_call_limit_enforced"] is False
        assert len(received) == 2
    finally:
        if call_token is not None:
            model_call.reset(call_token)
        if token is not None:
            research_work.reset(token)
        model.release.set()
        await runtime.bus.drain()
        runtime.close()


def test_legacy_budget_round_trip_keeps_original_allowance_and_accounting() -> None:
    original = {
        "record_kind": "ResearchBudget",
        "schema_version": "2.0.0",
        "max_calls": 24,
        "max_prompt_bytes": 180000,
        "max_reserved_tokens": 400000,
        "max_seconds": 900,
        "started_at": "2026-09-15T00:00:00+00:00",
        "calls": 2,
        "reserved_tokens": 500000,
        "actual_tokens": None,
        "actual_cost": None,
        "usage_state": "UNKNOWN",
    }
    assert ResearchBudget.model_validate(original).model_dump(mode="json") == original
