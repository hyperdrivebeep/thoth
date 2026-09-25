"""Validate committed external work, then publish an explicitly partial recovery result."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thoth.domain.canonical import canonical_payload, head_set_digest
from thoth.domain.enums import EntityType, ThreadExecutionState
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.post_execution_learning import PostExecutionLearningResult
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import (
    ResearchAttempt,
    ThreadRequestRevision,
)

if TYPE_CHECKING:
    from thoth.application.commands.research_threads import ResearchThreadHandlers


def require_committed_learning(
    host: ResearchThreadHandlers, attempt: ResearchAttempt, learning: PostExecutionLearningResult
) -> None:
    basis = learning.basis
    execution = basis.execution
    from thoth.application.services.research_freshness import ResearchFreshnessService

    freshness = ResearchFreshnessService(host.records.ledger)
    project = attempt.request_ref.project_id
    if (
        attempt.external_effect_state != "RETURNED"
        or attempt.external_effect_ref != execution.execution_attempt_ref
        or basis.project_id != project
        or basis.thread_id != attempt.continuation["thread_id"]
        or basis.request_ref != attempt.request_ref
    ):
        raise ValueError("COMMITTED_EFFECT_BINDING_REQUIRED")
    ref = execution.outcome_revision_ref
    revision = host.records.ledger.read_revision_by_digest(project, ref.revision_digest)
    snapshot = None if revision is None else host.records.ledger.read_snapshot(revision.snapshot_id)
    if (
        revision is None
        or snapshot is None
        or ref.project_id != project
        or revision.entity_type != EntityType.OUTCOME
        or revision.entity_id != ref.entity_id
        or revision.revision_id != ref.revision_id
        or host.records.ledger.read_heads(project).get(f"OUTCOME:{ref.entity_id}")
        != ref.revision_digest
    ):
        raise ValueError("COMMITTED_OUTCOME_REQUIRED")
    host.access.require_reads(
        project, (f"revision:{ref.revision_digest}", *execution.observation_refs)
    )
    outcome = OutcomeRecord.model_validate(snapshot.content)
    if freshness.owner_eligibility(project, ref.revision_digest).state != "CURRENT":
        raise ValueError("POST_EXECUTION_BASIS_REVIEW_REQUIRED")
    if (
        outcome.project_id != project
        or outcome.outcome_id != ref.entity_id
        or outcome.observed_evidence_refs != execution.observation_refs
        or set(outcome.test_validity_assessment_refs)
        != {a.assessment_id for a in execution.assessments}
    ):
        raise ValueError("COMMITTED_OUTCOME_BINDING_MISMATCH")
    for assessment in execution.assessments:
        if (
            host.assessment_verifier is None
            or assessment.attempt_ref != execution.execution_attempt_ref
            or canonical_payload(host.assessment_verifier(project, assessment.assessment_id))
            != canonical_payload(assessment)
        ):
            raise ValueError("COMMITTED_VALIDITY_REQUIRED")
    if not execution.observation_refs:
        raise ValueError("COMMITTED_OBSERVATION_REQUIRED")
    for identifier in execution.observation_refs:
        span = host.analysis.artifacts.read_evidence(identifier)
        artifact = None if span is None else host.analysis.artifacts.read_artifact(span.artifact_id)
        if (
            artifact is None
            or artifact.source_uri != f"sandbox://{execution.execution_attempt_ref}/result"
        ):
            raise ValueError("COMMITTED_OBSERVATION_ORIGIN_MISMATCH")


def finish_learning_recovery(
    host: ResearchThreadHandlers,
    attempt: ResearchAttempt,
    request: ThreadRequestRevision,
    work: ResearchWork,
    learning: PostExecutionLearningResult,
) -> None:
    with host.records.ledger.transaction():
        work.boundary.check()
        require_committed_learning(host, attempt, learning)
        previous = host.records.read(
            request.project_id, EntityType.DECISION_OBJECT, f"result:{request.thread_id}"
        )
        from thoth.domain.research_codec import decode_current_result_manifest

        manifest = None if previous is None else decode_current_result_manifest(previous[1])
        if manifest is not None and manifest.request_ref == attempt.request_ref:
            from thoth.domain.research_request import CurrentResultManifestV21

            if isinstance(manifest, CurrentResultManifestV21):
                work.consumed_heads.update(manifest.research_basis.consumed_heads)
                work.produced_refs.extend(manifest.research_basis.produced_refs)
                work.produced_receipt_refs.extend(manifest.research_basis.producer_receipt_refs)
                for key, digest in manifest.research_basis.produced_final_heads.items():
                    work.produced_final_heads.setdefault(key, digest)
                work.memory_revision_refs.extend(manifest.research_basis.memory_revision_refs)
                # Preserve the recorded producer receipts; never invent a new production basis.
                work.basis_reasons.extend(manifest.research_basis.reasons)
            else:
                work.basis_reasons.append("LEGACY_BASIS_UNKNOWN")
            work.context.update(manifest.result)
            work.record_refs.extend(
                ref for ref in manifest.record_refs if ref not in work.record_refs
            )
        # A crash cannot manufacture completion of later improvement/baseline work.
        result = {
            **work.context,
            "post_execution_learning": learning.model_dump(mode="json"),
            "committed_execution_basis": learning.basis.execution.model_dump(mode="json"),
            "execution_repeated": False,
            "recovery_scope": "POST_EXECUTION_LEARNING_ONLY",
            "research_state": "PARTIAL",
            "answer_status": "PARTIAL_HOLD",
            "postprocessing_not_verified_in_this_recovery": [
                "IMPROVEMENT_OBSERVATION",
                "BASELINE_REFRESH",
            ],
        }
        host.publish(
            attempt,
            request,
            work,
            "POST_EXECUTION_RECOVERY",
            result,
            ("POST_EXECUTION_REMAINING_STEPS_NOT_REPLAYED",),
            terminal="POST_EXECUTION_RECOVERED_PARTIAL",
        )
        thread = host.threads.read(request.thread_id)
        if thread is None or not host.threads.update(
            thread.model_copy(
                update={
                    "execution_state": ThreadExecutionState.IDLE,
                    "revision": thread.revision + 1,
                    "working_head_digest": head_set_digest(
                        host.records.ledger.read_heads(request.project_id)
                    ),
                    "updated_at": host.records.clock.now(),
                }
            ),
            expected_revision=thread.revision,
        ):
            raise ValueError("RECOVERED_THREAD_REVISION_CHANGED")
