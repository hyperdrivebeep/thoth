"""Normal question -> real adapter failure -> existing journal -> reopen -> thread/read."""

import inspect
import json
from pathlib import Path

import httpx
import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.unit.models.test_transport_diagnostic import (
    SECRETS,
    DiagnosticSession,
    FailingStream,
    assert_private,
    failure_response,
)

from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.adapters.models.codex_oauth import CodexOAuthModel
from thoth.adapters.models.registry import RegisteredModelResolver
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.apps.runtime import create_runtime
from thoth.domain.model_dispatch import ModelDispatchRecord
from thoth.domain.model_settings import ModelOption, ModelSelection


@pytest.mark.parametrize("kind", [httpx.ReadError, httpx.RemoteProtocolError, httpx.DecodingError])
async def test_normal_failure_keeps_diagnostic_private_and_thread_scoped_after_reopen(
    tmp_path: Path,
    kind: type[httpx.HTTPError],
):
    initial = await setup(tmp_path, ControlledResearchModel(), source=False)
    initial.close()
    calls: list[httpx.Request] = []
    original = kind(f"{SECRETS[4]} https://example.invalid/?key={SECRETS[3]}")
    original.__cause__ = OSError(104, SECRETS[4])
    stream = FailingStream(original)

    def serve(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return failure_response(stream)

    executor = CodexHttpExecutor(DiagnosticSession(), transport=httpx.MockTransport(serve))
    resolver = RegisteredModelResolver()
    resolver.register("codex-oauth", lambda _: CodexOAuthModel(executor))
    catalog = StaticModelCatalog(
        (
            ModelOption(
                provider="codex-oauth",
                model="diagnostic-fixture",
                reasoning_efforts=("high",),
                default_effort="high",
                capability_source="fixture",
            ),
        ),
        ModelSelection(provider="codex-oauth", model="diagnostic-fixture", reasoning_effort="high"),
    )
    runtime = create_runtime(tmp_path, model_resolver=resolver, model_catalog=catalog)
    try:
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "diagnostic",
                    {
                        "project_id": "p",
                        "problem": "Inspect connected records",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        read = request(
            "thread/read", "read", {"project_id": "p", "thread_id": admitted["thread_id"]}
        )
        state = value(await runtime.bus.query(read))
        assert state["current_result"]["terminal_reason"] == "OAUTH_TRANSPORT_FAILURE"
        assert len(calls) == 1 and stream.close_count == 1
        assert state["budget"]["calls"] == 1
        assert state["usage"]["state"] == "UNKNOWN" and state["usage"]["input_tokens"] is None
        dispatches = state["model_dispatches"]
        assert len(dispatches) == 1
        projected = dispatches[0]
        diagnostic = projected["transport_observation"]["transport_diagnostic"]
        assert diagnostic["httpx_error_type"] == kind.__name__
        assert diagnostic["nested_errno"] == 104
        assert projected["remote_stop"] == "UNKNOWN"
        assert_private(json.dumps(state))
        assert set(projected) == {
            "dispatch_id",
            "state",
            "received_bytes",
            "remote_stop",
            "transport_observation",
        }

        handler = runtime.bus._registry.resolve("thread/read")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        host = handler.__self__
        records = host.records
        stored = records.journal_read("p", projected["dispatch_id"], ModelDispatchRecord)
        assert stored is not None and stored.transport_observation is not None
        assert stored.transport_observation.transport_diagnostic is not None
        assert (
            stored.transport_observation.transport_diagnostic.model_dump(mode="json") == diagnostic
        )
        versions = [
            r
            for r in records.controls.list(
                "p", "RESEARCH_EXECUTION", "ModelDispatchRecord", latest_only=False
            )
            if r.record_id == projected["dispatch_id"]
        ]
        assert [r.payload["state"] for r in versions].count("OBSERVED") == 1
        assert [r.payload["state"] for r in versions].count("RESERVED") == 1
        for row in versions:
            assert_private(row.model_dump_json())
        records.journal(
            "p",
            "other-dispatch",
            stored.model_copy(
                update={
                    "dispatch_id": "other-dispatch",
                    "thread_id": "other-thread",
                    "operation_id": "other-operation",
                }
            ),
        )
        again = value(await runtime.bus.query(read))
        assert again["model_dispatches"] == dispatches
        assert len(calls) == 1
        legacy = stored.model_dump(mode="json")
        del legacy["transport_observation"]["transport_diagnostic"]
        old = ModelDispatchRecord.model_validate(legacy)
        assert old.transport_observation and old.transport_observation.transport_diagnostic is None
    finally:
        runtime.close()
    unused = ControlledResearchModel()
    reopened = create_runtime(tmp_path, model_resolver=unused, model_catalog=catalog)
    try:
        again = value(await reopened.bus.query(read))
        assert again["model_dispatches"] == dispatches
        assert again["usage"] == state["usage"] and again["budget"] == state["budget"]
        assert again["current_result"] == state["current_result"]
        assert_private(json.dumps(again))
        assert not unused.calls and len(calls) == 1
    finally:
        reopened.close()
