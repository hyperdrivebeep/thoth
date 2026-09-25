import json
from pathlib import Path
from types import MethodType
from typing import cast

import pytest
from pydantic import BaseModel
from tests.atomicity.harness import snapshot
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.commands.research_history import ResearchHistoryHandlers
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.research_history import (
    HistoricalResultInput,
    HistoricalResultView,
    SelectedEvidencePage,
)
from thoth.domain.research_request import ResearchAttempt, RevisionRef
from thoth.ports.model import ModelExecutionHold
from thoth.protocol.jsonrpc import JsonRpcResponse
from thoth.protocol.registry import MethodRegistry


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _strings(value: object) -> list[str]:
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in cast(list[object], value))
    return cast(list[str], value)


def _view(response: JsonRpcResponse) -> HistoricalResultView:
    assert response.error is None, response.error
    assert response.result is not None
    return HistoricalResultView.model_validate_json(
        json.dumps(_record(response.result["value"])), strict=True
    )


def _history_host(runtime: AppRuntime) -> ResearchHistoryHandlers:
    registry: object = vars(runtime.bus).get("_registry")
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve("thread/result/read")
    assert isinstance(bound, MethodType)
    owner = bound.__self__
    assert isinstance(owner, ResearchHistoryHandlers)
    return owner


class PartialModel(ControlledResearchModel):
    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        if request.role == ModelRole.HYPOTHESIS_GENERATOR:
            raise ModelExecutionHold("CONTROLLED_PARTIAL_HOLD")
        return await super().structured(request)


async def test_exact_partial_result_expands_only_on_opt_in_and_rejects_foreign_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = PartialModel()
    runtime = await setup(tmp_path, model)
    try:
        accepted_response = (
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "partial",
                    {"project_id": "p", "problem": "Explain alpha latency", "contract_version": 2},
                )
            )
        )
        assert accepted_response.error is None and accepted_response.result is not None
        accepted = _record(accepted_response.result["value"])
        await runtime.bus.drain()
        state_response = (
            await runtime.bus.query(
                request(
                    "thread/read",
                    "state",
                    {
                        "project_id": "p",
                        "thread_id": _text(accepted["thread_id"]),
                        "contract_version": 2,
                    },
                )
            )
        )
        assert state_response.error is None and state_response.result is not None
        state = _record(state_response.result["value"])
        request_ref = RevisionRef.model_validate_json(
            json.dumps(_record(state["request_ref"])), strict=True
        )
        attempt = ResearchAttempt.model_validate_json(
            json.dumps(_record(state["attempt"])), strict=True
        )
        checkpoint = attempt.checkpoint_ref
        assert checkpoint is not None
        inputs: dict[str, object] = {
            "project_id": "p",
            "thread_id": _text(accepted["thread_id"]),
            "request_revision_digest": request_ref.revision_digest,
            "result_revision_digest": checkpoint.revision_digest,
        }
        reader = _history_host(runtime).results
        expanded: list[str] = []
        original = reader.selected_evidence.read

        def observed(
            req: HistoricalResultInput, manifest: dict[str, object]
        ) -> SelectedEvidencePage:
            assert req.result_revision_digest is not None
            expanded.append(req.result_revision_digest)
            return original(req, manifest)

        monkeypatch.setattr(reader.selected_evidence, "read", observed)
        calls = len(model.calls)
        before = snapshot(runtime.ledger.engine)
        metadata = _view(await runtime.bus.query(request("thread/result/read", "metadata", inputs)))
        assert metadata.selected_evidence is None and expanded == []
        assert metadata.result is not None
        assert metadata.result["answer"] and "portfolio" not in metadata.result
        selected = _view(
            await runtime.bus.query(
                request(
                    "thread/result/read", "selected", {**inputs, "include_selected_evidence": True}
                )
            )
        )
        assert expanded == [inputs["result_revision_digest"]]
        page = selected.selected_evidence
        assert page is not None
        assert metadata.manifest is not None
        assert (
            page.selected_count > 0
            and [x.span_id for x in page.items] == _strings(metadata.manifest["source_refs"])
        )
        assert all(x.availability == "AVAILABLE" for x in page.items)
        for changed in (
            {"result_revision_digest": None},
            {"result_revision_digest": "f" * 64},
            {"request_revision_digest": "e" * 64},
            {"thread_id": "thread:foreign"},
            {"project_id": "foreign"},
        ):
            response = await runtime.bus.query(
                request(
                    "thread/result/read",
                    "invalid",
                    {**inputs, "include_selected_evidence": True, **changed},
                )
            )
            assert response.error is not None
        assert len(model.calls) == calls and snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
