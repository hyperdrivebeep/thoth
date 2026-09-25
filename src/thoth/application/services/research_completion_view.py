"""Compatible answer meaning; operation success remains a technical publication state."""

from thoth.application.services.request_records import RequestRecords
from thoth.application.services.research_stages import read_stage
from thoth.domain.evidence_requirements import CoverageAssessment
from thoth.domain.research_request import (
    CurrentResultManifest,
    CurrentResultManifestV21,
    ResearchAttempt,
    ResearchBudget,
)
from thoth.ports.resource_scope import ResourceAccessPort


def completion_view(
    records: RequestRecords,
    access: ResourceAccessPort,
    attempt: ResearchAttempt | None,
    manifest: CurrentResultManifest | CurrentResultManifestV21 | None,
    fresh: bool,
    operation_state: str,
    execution_state: str,
    budget: ResearchBudget | None,
) -> dict[str, object]:
    stages: list[dict[str, object]] = []
    if attempt is not None:
        for ref in attempt.completed_stage_refs:
            if ref.project_id != attempt.request_ref.project_id or not access.may_read_revision(
                ref.project_id, ref.revision_digest
            ):
                stages.append({"stage_ref": ref.model_dump(mode="json"), "state": "UNAVAILABLE"})
                continue
            stage = read_stage(records, ref)
            if stage.request_ref != attempt.request_ref:
                stages.append({"stage_ref": ref.model_dump(mode="json"), "state": "OTHER_REQUEST"})
                continue
            stages.append(
                {
                    "stage_ref": ref.model_dump(mode="json"),
                    "state": stage.state,
                    "role": stage.role,
                    "input_basis_digest": stage.input_basis_digest,
                    "output_digest": stage.output_payload_digest,
                    "context_bytes": stage.context_bytes,
                    "elapsed_ms": stage.elapsed_ms,
                    "dispatch_ids": list(stage.dispatch_ids),
                    "automatic_reuse_supported": False,
                }
            )
    result = {} if manifest is None else manifest.result
    has_answer = isinstance(result.get("answer"), str) and bool(str(result["answer"]).strip())
    kind = "NOT_PRODUCED"
    if manifest is not None:
        if (
            manifest.phase == "HOLD"
            or operation_state == "FAILED"
            or result.get("answer_status") == "PARTIAL_HOLD"
        ):
            kind = "HOLD"
        elif not fresh:
            kind = (
                "REVIEW_REQUIRED"
                if isinstance(manifest, CurrentResultManifestV21)
                else "LEGACY_UNKNOWN"
            )
        elif has_answer:
            kind = "PARTIAL"
            assessed = False
            for ref in manifest.record_refs:
                if (
                    ref.project_id != manifest.request_ref.project_id
                    or not access.may_read_revision(ref.project_id, ref.revision_digest)
                ):
                    continue
                revision = records.ledger.read_revision_by_digest(
                    ref.project_id, ref.revision_digest
                )
                snapshot = (
                    None if revision is None else records.ledger.read_snapshot(revision.snapshot_id)
                )
                if snapshot is None or snapshot.content.get("record_kind") != "CoverageAssessment":
                    continue
                coverage = CoverageAssessment.model_validate(snapshot.content)
                assessed = (
                    coverage.request_ref == manifest.request_ref
                    and not coverage.reasons
                    and not any(v == "HOLD" for v in coverage.gates.values())
                )
            if (
                assessed
                and manifest.phase == "COMPLETE"
                and manifest.completion == "TERMINAL"
                and result.get("answer_status") == "ASSESSED_FOR_REQUEST"
            ):
                kind = "ASSESSED_FOR_REQUEST"
            elif manifest.source_context_version is None:
                kind = "LEGACY_UNKNOWN"
    terminal = operation_state in {"SUCCEEDED", "FAILED", "CANCELLED"}
    return {
        "answer_outcome": {
            "schema_version": "1.0.0",
            "state": kind,
            "has_answer": has_answer,
            "basis_current": fresh,
            "scientific_truth_certified": False,
            "terminal_reason": None if manifest is None else manifest.terminal_reason,
        },
        "resume_information": {
            "schema_version": "1.0.0",
            "same_attempt_after_terminal_supported": False,
            "support": "SAME_ATTEMPT_NOT_SUPPORTED"
            if terminal
            else "EXISTING_PAUSED_ATTEMPT"
            if execution_state == "PAUSED"
            else "RUNNING"
            if operation_state == "RUNNING"
            else "UNKNOWN",
            "additional_budget_required": False,
            "automatic_new_budget": False,
            "new_input_with_existing_budget_possible": budget is not None,
            "provider_acceptance_unknown": True,
            "research_usage_policy": "OBSERVATION_ONLY",
            "checkpoint_ref": None
            if attempt is None or attempt.checkpoint_ref is None
            else attempt.checkpoint_ref.model_dump(mode="json"),
        },
        "completed_stages": stages,
    }
