"""Actual reviewer input, outside witness, result publication and legacy card behavior."""

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel
from tests.atomicity.harness import assert_phase_delta
from tests.atomicity.harness import snapshot as database_snapshot
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.apps.runtime import create_runtime
from thoth.domain.absence_claim import ABSENCE_INSTRUCTION, AbsenceAssessments, ReviewProposalE2
from thoth.domain.canonical import canonical_payload, model_digest
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import ResearchAttempt, RevisionRef, ThreadRequestRevision

TEXT = (
    "# Measurements\n\n## Control\n\ncontrol 100 n 1s\ncontrol 1000 n 2s\n\n"
    "## Polars 1.44.1\n\nfill_nan 100 n 0.002s\nfill_nan 1000 n 0.025s\n\n"
    "## Unrelated\n\nAnother source paragraph outside the selected evidence.\n"
)
ANSWER = "There is no Polars 1.44.1. There is no 반복 분포. There is no AbsentExactToken."


class AbsenceModel(ControlledResearchModel):
    def __init__(self, outside: bool = False) -> None:
        super().__init__(one=True)
        self.outside = outside

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        result = await super().structured(request)
        data = result.output.model_dump(mode="json")
        if request.role == ModelRole.EVIDENCE_RERANKER and self.outside:
            data["ordered_span_ids"] = [
                s.span_id
                for s in request.context_pack.evidence
                if s.exact_text == "control 100 n 1s"
            ]
            assert len(data["ordered_span_ids"]) == 1
        elif request.role == ModelRole.SEMANTIC_REVIEWER:
            assert request.output_model is ReviewProposalE2
            assert (
                request.context_pack.research_context["absence_instruction"] == ABSENCE_INSTRUCTION
            )
            data["answer"] = ANSWER
            refs = [request.context_pack.evidence[0].span_id]
            data["confirmed_findings"] = [
                {
                    "statement": "There is no Polars 1.44.1.",
                    "evidence_refs": refs,
                    "kind": "VALID_NEGATIVE_FINDING",
                },
                {
                    "statement": "The source contains measurements.",
                    "evidence_refs": refs,
                    "kind": "DOCUMENT_FACT",
                },
            ]
            data["absence_claims"] = [
                {
                    "statement": f"There is no {target}.",
                    "target": target,
                    "target_language_basis": basis,
                    "kind": "LITERAL",
                    "claimed_scope": "ALL_MATERIAL",
                    "origin": "CONFIRMED_FINDING" if index == 0 else "ANSWER",
                    "finding_index": 0 if index == 0 else None,
                }
                for index, (target, basis) in enumerate(
                    [
                        ("Polars 1.44.1", "SOURCE_LITERAL"),
                        ("반복 분포", "TRANSLATED"),
                        ("AbsentExactToken", "SOURCE_LITERAL"),
                    ]
                )
            ]
        else:
            return result
        output = request.output_model.model_validate(data)
        return replace(
            result,
            output=output,
            output_digest=model_digest("OUTPUT", output, schema_version="1.0.0"),
        )


@pytest.mark.parametrize("outside", [False, True])
async def test_dual_scope_warnings_preserve_answer_and_block_only_the_bound_card(
    tmp_path: Path,
    outside: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = AbsenceModel(outside)
    runtime = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    host = research_host(runtime)
    publish = host.publish
    rejected = False

    def guarded_publish(
        attempt: ResearchAttempt,
        accepted: ThreadRequestRevision,
        work: ResearchWork,
        phase: str,
        payload: dict[str, object],
        gaps: tuple[str, ...],
        terminal: str | None = None,
        check: bool = True,
    ) -> RevisionRef:
        nonlocal rejected
        if not rejected and "absence_assessments" in payload:
            bad = AbsenceAssessments.model_validate(payload["absence_assessments"]).model_dump(
                mode="json"
            )
            bad["items"][0]["claim"]["target"] = "Modified after checking"
            before = database_snapshot(runtime.ledger.engine)
            pending = tuple(work.pending_revisions)
            with pytest.raises(ValueError, match="ABSENCE_CLAIM_BINDING_MISMATCH"):
                publish(
                    attempt,
                    accepted,
                    work,
                    phase,
                    {**payload, "absence_assessments": bad},
                    gaps,
                    terminal,
                    check,
                )
            assert_phase_delta(before, database_snapshot(runtime.ledger.engine))
            assert tuple(work.pending_revisions) == pending
            rejected = True
        return publish(attempt, accepted, work, phase, payload, gaps, terminal, check)

    monkeypatch.setattr(host, "publish", guarded_publish)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "create",
                    {
                        "project_id": "p",
                        "name": "Literal checks",
                        "cutoff_at": "2026-10-27T00:00:00Z",
                    },
                )
            )
        )
        inbox = tmp_path / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "source.md").write_text(TEXT, encoding="utf-8")
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "connect",
                    {
                        "project_id": "p",
                        "relative_path": "source.md",
                        "media_type": "text/markdown",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Compare the printed measurements.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "full",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                        "view": "FULL",
                        "expected_result_contract": "v5",
                    },
                )
            )
        )
        assert state["operation_state"] == "SUCCEEDED", state.get("operation_error")
        assert rejected
        result = state["current_result"]["result"]
        assert result["answer"] == ANSWER
        assert result["answer_status"] == "PARTIAL_HOLD"
        assert [f["statement"] for f in result["confirmed_findings"]] == [
            "The source contains measurements."
        ]
        assert result["confirmed_findings_dropped"] == 1
        assert "checked_observations" in result
        envelope = AbsenceAssessments.model_validate(result["absence_assessments"])
        assert envelope.items[0].effective == (
            "FOUND_OUTSIDE_MODEL_INPUT" if outside else "FOUND_IN_MODEL_INPUT"
        )
        assert envelope.items[0].injection_state == ("DELIVERED" if outside else "NOT_NEEDED")
        assert envelope.items[1].effective == "UNSUPPORTED"
        assert envelope.items[1].claim.target == "반복 분포"
        assert envelope.items[2].effective == "NOT_FOUND_WITHIN_SCOPES"
        assert len(model.calls) == 7
        reviewer = next(c for c in model.calls if c.role == ModelRole.SEMANTIC_REVIEWER)
        adjudicator = next(c for c in model.calls if c.role == ModelRole.REVIEW_ADJUDICATOR)
        a = next(s for s in envelope.scope_sets if s.kind == "ACTUAL_ROLE_INPUT")
        b = next(s for s in envelope.scope_sets if s.kind == "ALLOWED_EXTRACTED_AT_REQUEST")
        assert tuple(m.span_id for m in a.members) == tuple(
            s.span_id for s in reviewer.context_pack.evidence
        )
        assert b.capture_phase == "ANALYSIS_START_BEFORE_MEMORY_EXPANSION"
        view = cast(dict[str, object], adjudicator.context_pack.research_context["absence_checks"])
        assert view["coverage"] == "DECLARED_CLAIMS_ONLY"
        supplied = {s.span_id for s in adjudicator.context_pack.evidence}
        assert set(envelope.items[0].b_result.witness_refs) <= supplied
        assert set(a.checked_refs) <= supplied
        if outside:
            assert not set(envelope.items[0].b_result.witness_refs) & set(a.checked_refs)
            assert set(b.checked_refs) - set(state["current_result"]["source_refs"])
        consumed = {s["span_id"] for s in state["current_result"]["research_basis"]["source_basis"]}
        assert set(b.checked_refs) <= consumed
        ref = envelope.active_stage_ref
        revision = runtime.ledger.read_revision_by_digest("p", ref.revision_digest)
        assert revision is not None
        snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snapshot is not None
        stored_stage = snapshot.content
        assert stored_stage["output_codec"] == "ReviewProposalE2"
        original = ReviewProposalE2.model_validate(cast(dict[str, object], stored_stage["output"]))
        assert (
            original.answer == ANSWER
            and original.confirmed_findings[0].kind == "VALID_NEGATIVE_FINDING"
        )
        before = canonical_payload(envelope)
        summary = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "summary",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                    },
                )
            )
        )
        slim = summary["current_result"]["result"]["absence_assessments"]
        assert "scope_sets" not in slim and slim["items"][0]["target"] == "Polars 1.44.1"
        assert canonical_payload(envelope) == before
    finally:
        runtime.close()


class KoreanAbsenceModel(ControlledResearchModel):
    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        result = await super().structured(request)
        if request.role != ModelRole.SEMANTIC_REVIEWER:
            return result
        data = result.output.model_dump(mode="json")
        statement = "자료에 버전 제목이 없습니다."
        data.update(
            answer=statement,
            confirmed_findings=[
                {
                    "statement": statement,
                    "kind": "VALID_NEGATIVE_FINDING",
                    "evidence_refs": [request.context_pack.evidence[0].span_id],
                }
            ],
            absence_claims=[
                {
                    "statement": statement,
                    "target": "버전 제목",
                    "target_language_basis": "SOURCE_LITERAL",
                    "kind": "LITERAL",
                    "claimed_scope": "ALL_MATERIAL",
                    "origin": "CONFIRMED_FINDING",
                    "finding_index": 0,
                }
            ],
        )
        output = request.output_model.model_validate(data)
        return replace(
            result,
            output=output,
            output_digest=model_digest("OUTPUT", output, schema_version="1.0.0"),
        )


async def test_hangul_source_particle_blocks_false_card_and_preserves_original_answer(
    tmp_path: Path,
) -> None:
    model = KoreanAbsenceModel(one=True)
    runtime = await setup(tmp_path, model, source=False)
    try:
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "korean.md").write_text(
            "# 원문\n\n버전 제목은 69행에 있다\n", encoding="utf-8"
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": "p",
                        "relative_path": "korean.md",
                        "media_type": "text/markdown",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "버전 제목이 있는가?",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        state = value(
            await runtime.bus.query(
                request(
                    "thread/read",
                    "read",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                        "view": "FULL",
                        "expected_result_contract": "v5",
                    },
                )
            )
        )
        assert state["operation_state"] == "SUCCEEDED", state.get("operation_error")
        result = state["current_result"]["result"]
        checked = AbsenceAssessments.model_validate(result["absence_assessments"])
        assert checked.checker_version == "literal-absence:1.1.0"
        assert checked.items[0].a_result.outcome == checked.items[0].b_result.outcome == "FOUND"
        assert checked.items[0].claim.target == "버전 제목"
        assert result["answer"] == "자료에 버전 제목이 없습니다."
        assert result["answer_status"] == "PARTIAL_HOLD"
        assert result["confirmed_findings"] == [] and result["confirmed_findings_dropped"] == 1
        assert len(model.calls) == 7
    finally:
        runtime.close()
