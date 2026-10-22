"""A real post-research reference rejection must describe only readable saved research."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from tests.integration.reference_helpers import reference_harness
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model, StaticModelResolver
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.application.commands.reference import ReferenceThreadEntry
from thoth.application.commands.thread_analysis import ThreadAnalysisCommandHandlers
from thoth.application.services.revision_service import CommitResult
from thoth.domain.behavior_execution import BehaviorExecutionRecord
from thoth.domain.reference import ReferenceError
from thoth.protocol.deferred import current_operation


def save(name: str, evidence: dict[str, Any]) -> None:
    folder = (
        Path(__file__).resolve().parents[2]
        / "outputs/refactor-p-20261022"
        / os.environ.get("THOTH_P_RUN_LABEL", "p3-direct")
    )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(
        json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8"
    )


def install_models(monkeypatch: pytest.MonkeyPatch, version: int) -> list[str]:
    controlled = ControlledResearchModel(one=True)
    calls: list[str] = []
    dynamic = DynamicA02Model.structured
    structured = ControlledResearchModel.structured

    async def legacy_model(instance: DynamicA02Model, call: Any) -> Any:
        calls.append(call.role.value)
        return await dynamic(instance, call)

    async def controlled_model(instance: ControlledResearchModel, call: Any) -> Any:
        calls.append(call.role.value)
        return await structured(instance, call)

    def resolve(
        instance: StaticModelResolver, *, provider: str, model: str | None
    ) -> ControlledResearchModel:
        return controlled

    monkeypatch.setattr(DynamicA02Model, "structured", legacy_model)
    monkeypatch.setattr(ControlledResearchModel, "structured", controlled_model)
    if version == 2:
        monkeypatch.setattr(StaticModelResolver, "resolve", resolve)
    return calls


@pytest.mark.parametrize("version", [1, 2])
async def test_real_reference_rejection_reports_saved_research_in_both_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    async with reference_harness(tmp_path) as h:
        calls = install_models(monkeypatch, version)
        captured: dict[str, Any] = {}
        original = ReferenceThreadEntry._reference  # pyright: ignore[reportPrivateUsage]

        async def reference(entry: ReferenceThreadEntry, selected: Any, result: Any) -> Any:
            op = current_operation.get()
            assert op is not None
            captured["operation_id"] = op.operation_id
            captured["delegate_result"] = result
            return await original(entry, selected, result)

        monkeypatch.setattr(ReferenceThreadEntry, "_reference", reference)
        command = request(
            "thread/input",
            "p3-missing-inquiry",
            {
                "project_id": h.project,
                "thread_id": h.thread,
                "instruction": "Compare the saved source measurements.",
                **({"contract_version": 2} if version == 2 else {}),
                "reference_request": {**h.request, "inquiry_id": "missing:p3-inquiry"},
            },
        )
        response = await h.runtime.bus.dispatch(command)
        await h.runtime.bus.drain()
        assert captured, response
        operation = h.runtime.bus.read_operation(captured["operation_id"])
        assert operation is not None and operation.error is not None
        error = operation.error
        commit = CommitResult.model_validate(captured["delegate_result"]["commit"])
        assert commit.receipt in h.runtime.ledger.read_receipts(h.project)
        assert commit.committed_revision_ids
        readable: list[dict[str, Any]] = []
        for identifier in commit.committed_revision_ids:
            revision = h.runtime.ledger.read_revision_by_id(h.project, identifier)
            assert revision is not None
            read = value(
                await h.runtime.bus.dispatch(
                    request(
                        "revision/read",
                        "p3-read-" + identifier,
                        {"project_id": h.project, "revision_digest": revision.revision_digest},
                    )
                )
            )
            content = value(
                await h.runtime.bus.dispatch(
                    request(
                        "revision/content/read",
                        "p3-content-" + identifier,
                        {"project_id": h.project, "content_digest": read["content_digest"]},
                    )
                )
            )
            readable.append({"revision": read, "content": content})
        current = value(
            await h.runtime.bus.dispatch(
                request(
                    "thread/read",
                    "p3-thread",
                    {"project_id": h.project, "thread_id": h.thread, "view": "FULL"},
                )
            )
        )
        captured.update(error=error, readable=readable, thread_read=current)
        save(f"reference-v{version}", captured)
        assert error["code"] == (-32030 if version == 1 else -32603)
        data = error["data"]
        assert isinstance(data, dict)
        assert data["reason_code"] == "REFERENCE_INQUIRY_NOT_FOUND"
        assert data.get("research_saved") is True
        message = str(error["message"])
        assert "기준(reference) 단계만 실패했습니다" in message
        assert "Only the reference step failed" in message
        if version == 2:
            failure = data["failure"]
            assert isinstance(failure, dict)
            primary = failure["primary"]
            assert isinstance(primary, dict)
            assert primary["exception_type"] == "RpcApplicationError"
            manifest = current.get("current_result") or current.get("previous_result")
            assert manifest is not None and manifest["completion"] != "TERMINAL"
            assert "중간 기록" in message
        before_replay_calls = len(calls)
        assert before_replay_calls > 0
        replay = await h.runtime.bus.dispatch(command)
        await h.runtime.bus.drain()
        assert replay.error is not None
        assert replay.error.model_dump(mode="json") == error
        assert len(calls) == before_replay_calls


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("failure", ["before", "delegate", "unproven", "generic"])
async def test_other_failures_never_claim_saved_reference_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int, failure: str
) -> None:
    async with reference_harness(tmp_path) as h:
        calls = install_models(monkeypatch, version)
        captured: dict[str, Any] = {}
        original_entry = ReferenceThreadEntry.analyze
        original_delegate = ThreadAnalysisCommandHandlers.analyze

        async def entry(owner: ReferenceThreadEntry, payload: Any) -> Any:
            operation = current_operation.get()
            assert operation is not None
            captured["operation_id"] = operation.operation_id
            captured["calls_at_reference"] = list(calls)
            return await original_entry(owner, payload)

        async def delegate(owner: ThreadAnalysisCommandHandlers, payload: Any) -> Any:
            if failure == "delegate":
                raise ReferenceError("REFERENCE_DELEGATE_TEST_FAILURE")
            result = await original_delegate(owner, payload)
            if failure == "unproven":
                result = {k: v for k, v in result.items() if k != "commit"}
            return result

        async def generic(owner: ReferenceThreadEntry, selected: Any, result: Any) -> Any:
            raise RuntimeError("private-test-marker-never-present-in-error")

        monkeypatch.setattr(ReferenceThreadEntry, "analyze", entry)
        monkeypatch.setattr(ThreadAnalysisCommandHandlers, "analyze", delegate)
        if failure == "generic":
            monkeypatch.setattr(ReferenceThreadEntry, "_reference", generic)
        selected = {**h.request, "inquiry_id": "missing:p3-inquiry"}
        if failure == "before":
            selected["expected_revision_digest"] = "0" * 64
        response = await h.input(
            "p3-negative",
            instruction="Compare the saved source measurements.",
            reference_request=selected,
            **({"contract_version": 2} if version == 2 else {}),
        )
        await h.runtime.bus.drain()
        assert captured, response
        operation = h.runtime.bus.read_operation(captured["operation_id"])
        assert operation is not None and operation.error is not None
        error = operation.error
        data = error.get("data") or {}
        assert isinstance(data, dict)
        assert "research_saved" not in data
        assert "저장됐습니다" not in str(error["message"])
        assert "were saved" not in str(error["message"])
        assert "private-test-marker" not in str(error)
        if version == 2:
            failure_record = data["failure"]
            assert isinstance(failure_record, dict)
            primary = failure_record["primary"]
            assert isinstance(primary, dict)
            assert primary["exception_type"] == (
                "RuntimeError" if failure == "generic" else "RpcApplicationError"
            )
        if failure in {"before", "delegate"}:
            assert calls == captured["calls_at_reference"]
            assert calls == (
                ["RESEARCH_PLANNER", "EVIDENCE_RERANKER", "SEMANTIC_REVIEWER", "REVIEW_ADJUDICATOR"]
                if version == 2
                else []
            )
        else:
            assert calls
        save(f"negative-{version}-{failure}", {"error": error, "model_roles": calls})


@pytest.mark.parametrize("result", [{"queued": True}, {"unrelated_result": True}])
async def test_queued_and_unassessed_results_do_not_enter_reference_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: dict[str, bool]
) -> None:
    async with reference_harness(tmp_path) as h:

        async def delegate(owner: ThreadAnalysisCommandHandlers, payload: Any) -> Any:
            return result

        async def reference(owner: ReferenceThreadEntry, selected: Any, payload: Any) -> Any:
            raise AssertionError("reference must not run for queued/unassessed input")

        monkeypatch.setattr(ThreadAnalysisCommandHandlers, "analyze", delegate)
        monkeypatch.setattr(ReferenceThreadEntry, "_reference", reference)
        response = await h.input("p3-no-reference", reference_request=h.request)
        observed = value(response)
        if result.get("queued") is True:
            assert observed == result
        else:
            assert observed.keys() - result.keys() == {"behavior_execution"}
            behavior = BehaviorExecutionRecord.model_validate(observed["behavior_execution"])
            assert (behavior.project_id, behavior.thread_id, behavior.state) == (
                h.project,
                h.thread,
                "COMPLETED",
            )
            assert {k: v for k, v in observed.items() if k != "behavior_execution"} == result
