"""A progress callback sees the complete selected basis, without relaxing source fences."""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import MethodType
from typing import cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.canonical import model_digest
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_graph import SpanCorrectionRecord
from thoth.domain.evidence_requirements import EvidenceRanking
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.operation import OperationRecord
from thoth.domain.research_execution import ResearchFence, research_work
from thoth.domain.research_request import ResearchAttempt
from thoth.protocol.registry import MethodRegistry


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _sequence(value: object) -> Sequence[object]:
    assert isinstance(value, (list, tuple))
    return cast(Sequence[object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _integer(value: object) -> int:
    assert type(value) is int
    return value


def research_host(runtime: AppRuntime) -> ResearchThreadHandlers:
    registry = vars(runtime.bus).get("_registry")
    assert isinstance(registry, MethodRegistry)
    handler = registry.resolve("thread/read")
    assert isinstance(handler, MethodType)
    host = handler.__self__
    assert isinstance(host, ResearchThreadHandlers)
    return host


class SelectingModel(ControlledResearchModel):
    mutate: Callable[[tuple[EvidenceSpan, ...]], None] | None = None

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        result = await super().structured(request)
        if request.role == ModelRole.EVIDENCE_RERANKER:
            assert request.output_model is EvidenceRanking
            if self.mutate is not None:
                self.mutate(request.context_pack.evidence)
            selected = EvidenceRanking(
                ordered_span_ids=tuple(
                    s.span_id for s in request.context_pack.evidence if "primary" in s.exact_text
                ),
                rationale="Controlled selection of a strict subset of artifacts",
            )
            return replace(
                result,
                output=request.output_model.model_validate(selected),
                output_digest=model_digest("OUTPUT", selected, schema_version="1.0.0"),
            )
        return result


async def catalog(tmp_path: Path, model: SelectingModel) -> AppRuntime:
    runtime = await setup(tmp_path, model, source=False)
    (tmp_path / "inbox").mkdir(exist_ok=True)
    for label, count in (("primary", 118), ("secondary", 42)):
        (tmp_path / "inbox" / f"{label}.md").write_text(
            "\n".join(
                f"{label} latency measurement record {i}: 12 ms under alpha condition"
                for i in range(count)
            ),
            encoding="utf-8",
        )
        connected = await runtime.bus.dispatch(
            request(
                "project/source/connect",
                label,
                {
                    "project_id": "p",
                    "relative_path": f"{label}.md",
                    "media_type": "text/markdown",
                    "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
        assert connected.error is None and connected.result is not None
        _record(connected.result["value"])
    return runtime


async def start(runtime: AppRuntime) -> OperationRecord:
    response = await runtime.bus.dispatch(
        request(
            "thread/start",
            "focus-basis",
            {
                "project_id": "p",
                "problem": "Explain latency measurements under alpha condition",
                "contract_version": 2,
            },
        )
    )
    assert response.error is None and response.result is not None
    accepted = _record(response.result["value"])
    await runtime.bus.drain()
    operation = runtime.bus.read_operation(_text(accepted["operation_id"]))
    assert operation is not None
    return operation


async def test_selected_progress_basis_is_consistent_and_reaches_semantic_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = SelectingModel()
    runtime = await catalog(tmp_path, model)
    try:
        host = research_host(runtime)
        original = host.records.journal
        observed: list[set[str]] = []

        def journal(
            project: str, key: str, payload: BaseModel, state: str = "RUNNING"
        ) -> None:
            if isinstance(payload, ResearchAttempt) and payload.phase == "EVIDENCE_FOCUS":
                work = research_work.get()
                assert work is not None
                ids = {s.span_id for s in work.evidence}
                assert len(ids) == 118
                assert work.context["source_context_digest"] == host.analysis.source_digest(
                    work.evidence
                )
                assert work.context["source_packet"] == host.analysis.source_packet(work.evidence)
                bundle = _record(work.context["context_bundle"])
                assert {
                    _text(ref)
                    for item in _list(bundle["bundles"])
                    for ref in _sequence(_record(item)["span_refs"])
                } == ids
                waves = _list(work.context["internal_expansion_waves"])
                assert _integer(_record(waves[-1])["selected_count"]) == 118
                retrieval = _record(work.context["retrieval"])
                assert _integer(retrieval["candidate_count"]) == 160
                assert _integer(retrieval["selected_count"]) == 118
                assert work.context["source_context_version"] == "2.1.0"
                assert "gap_targets" in work.context
                observed.append(ids)
            return original(project, key, payload, state)

        monkeypatch.setattr(host.records, "journal", journal)
        operation = await start(runtime)
        assert operation.state.value == "SUCCEEDED", operation.error
        assert observed
        reviewer = next(call for call in model.calls if call.role == ModelRole.SEMANTIC_REVIEWER)
        assert {s.span_id for s in reviewer.context_pack.evidence} == observed[0]
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["span", "source_version", "correction"])
async def test_real_source_change_after_reranker_still_fences_before_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    model = SelectingModel()
    runtime = await catalog(tmp_path, model)
    try:
        host = research_host(runtime)
        analysis = host.analysis
        reasons: list[str] = []
        original_check = analysis.require_current_sources

        def checked(
            evidence: tuple[EvidenceSpan, ...], expected_digest: object = None
        ) -> None:
            try:
                return original_check(evidence, expected_digest)
            except ResearchFence as exc:
                reasons.append(str(exc))
                raise

        monkeypatch.setattr(analysis, "require_current_sources", checked)

        def mutate(candidates: tuple[EvidenceSpan, ...]) -> None:
            target = candidates[0]
            if change == "correction":
                analysis.evidence_graph.add_span_correction(
                    SpanCorrectionRecord(
                        correction_id="correction:changed-during-model",
                        project_id="p",
                        span_id=target.span_id,
                        corrected_text="New correction after captured basis",
                        reason="Controlled concurrent change",
                        correction_digest="b" * 64,
                        created_at=datetime(2026, 9, 22, tzinfo=UTC),
                    )
                )
            else:
                text = target.exact_text + " changed"
                changed = target.model_copy(
                    update=(
                        {
                            "exact_text": text,
                            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                        }
                        if change == "span"
                        else {"source_version_id": "source-version:changed"}
                    )
                )
                original_evidence = analysis.evidence
                def changed_evidence(project: str) -> tuple[EvidenceSpan, ...]:
                    return tuple(
                        changed if s.span_id == target.span_id else s
                        for s in original_evidence(project)
                    )

                monkeypatch.setattr(analysis, "evidence", changed_evidence)

        model.mutate = mutate
        operation = await start(runtime)
        assert operation.state.value == "CANCELLED"
        assert reasons == [
            "SOURCE_CONTEXT_CHANGED" if change == "correction" else "SOURCE_BASIS_CHANGED"
        ]
        assert [call.role for call in model.calls] == [
            ModelRole.RESEARCH_PLANNER,
            ModelRole.EVIDENCE_RERANKER,
        ]
    finally:
        runtime.close()
