"""Prepare cycle records and their identities; the caller owns publication."""

from collections.abc import Callable

from thoth.application.services.criterion_projection import criterion_projection
from thoth.application.workflows.thread_cycle_contracts import ThreadCycleCommand
from thoth.domain.action import ActionPlan
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.criterion import CriterionCandidate
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import EntityType, OutcomeStatus
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.memory import MemoryRecord
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.research_identity import ResearchIdentityError, decode_research
from thoth.domain.revision import EntitySnapshot, SemanticRevision, StagedRevision
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


def reanalysis_outcome(
    command: ThreadCycleCommand, plan: ActionPlan, head_digest: str, *, clock: ClockPort
) -> OutcomeRecord:
    return OutcomeRecord(
        outcome_id=f"outcome:{command.thread_id}",
        project_id=command.project_id,
        action_id=plan.frontier[0] if plan.frontier else plan.alternatives[0].action_id,
        status=OutcomeStatus.OBSERVED,
        observed_evidence_refs=tuple(span.span_id for span in command.evidence),
        hypothesis_updates=(),
        interpretation=(
            "Reanalysis was recorded after connected evidence changed; causal attribution "
            "has not been independently established."
        ),
        limitations=("This outcome record does not certify causality or scientific truth.",),
        recorded_at=clock.now(),
        input_head_set_digest=head_digest,
    )


def stage_revision(
    command: ThreadCycleCommand,
    entity_type: EntityType,
    entity_id: str,
    content: dict[str, object],
    evidence: tuple[EvidenceSpan, ...],
    heads: dict[str, str],
    *,
    ids: IdGeneratorPort,
    clock: ClockPort,
) -> StagedRevision:
    schema_version = str(content.get("schema_version", "1.0.0"))
    content_digest = domain_digest("SNAPSHOT", schema_version, canonical_payload(content))
    snapshot = EntitySnapshot(
        snapshot_id=ids.new("snapshot"),
        project_id=command.project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        schema_version=schema_version,
        content=content,
        content_digest=content_digest,
    )
    aggregate_key = f"{entity_type.value}:{entity_id}"
    current = heads.get(aggregate_key)
    revision_id = ids.new("revision")
    created_at = clock.now()
    revision_payload: dict[str, object] = {
        "revision_id": revision_id,
        "project_id": command.project_id,
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "snapshot_id": snapshot.snapshot_id,
        "parent_revision_digests": [] if current is None else [current],
        "actor": command.actor,
        "reason": f"thread cycle {command.thread_id}",
        "evidence_refs": [span.span_id for span in evidence],
        "affected_refs": [],
        "created_at": created_at,
        "schema_version": schema_version,
    }
    revision = SemanticRevision.model_validate(
        {
            **revision_payload,
            "revision_digest": domain_digest(
                "SEMANTIC_REVISION", schema_version, canonical_payload(revision_payload)
            ),
        }
    )
    return StagedRevision(snapshot=snapshot, revision=revision)


def prepare_memory(
    project_id: str, staged: tuple[StagedRevision, ...], *, ids: IdGeneratorPort
) -> tuple[MemoryRecord, ...]:
    from thoth.application.services.domain_reference_memory import domain_reference_memory

    return tuple(
        record
        for item in staged
        if (
            record := domain_reference_memory(
                item.revision, ids.new("memory"), item.snapshot.content
            )
        )
        is not None
    )


def stage_criteria(
    command: ThreadCycleCommand,
    heads: dict[str, str],
    *,
    ledger: LedgerPort,
    stage: Callable[
        [
            ThreadCycleCommand,
            EntityType,
            str,
            dict[str, object],
            tuple[EvidenceSpan, ...],
            dict[str, str],
        ],
        StagedRevision,
    ],
) -> tuple[StagedRevision, ...]:
    staged: list[StagedRevision] = []
    for candidate in command.criteria:
        if candidate.project_id != command.project_id:
            raise ResearchIdentityError("CRITERION_PROJECT_SCOPE_MISMATCH")
        head = heads.get(f"CRITERION:{candidate.criterion_id}")
        if candidate.source_contract_revision is not None:
            if head != candidate.source_contract_revision:
                raise ResearchIdentityError("CRITERION_OWNER_REVISION_CHANGED")
            revision = ledger.read_revision_by_digest(
                command.project_id, candidate.source_contract_revision
            )
            snapshot = None if revision is None else ledger.read_snapshot(revision.snapshot_id)
            if revision is None or snapshot is None:
                raise ResearchIdentityError("CRITERION_OWNER_MISSING")
            record = decode_research(revision, snapshot).record
            if (
                not isinstance(record, CriterionContractRecord)
                or criterion_projection(record) != candidate
            ):
                raise ResearchIdentityError("CRITERION_OWNER_VIEW_MISMATCH")
            from thoth.application.services.research_basis_capture import capture_consumed

            capture_consumed(
                command.project_id,
                f"CRITERION:{candidate.criterion_id}",
                revision.revision_digest,
            )
            continue
        if head is not None:
            revision = ledger.read_revision_by_digest(command.project_id, head)
            snapshot = None if revision is None else ledger.read_snapshot(revision.snapshot_id)
            if revision is None or snapshot is None:
                raise ResearchIdentityError("CRITERION_OWNER_MISSING")
            if not isinstance(decode_research(revision, snapshot).record, CriterionCandidate):
                raise ResearchIdentityError("CRITERION_OWNER_BINDING_REQUIRED")
        staged.append(
            stage(
                command,
                EntityType.CRITERION,
                candidate.criterion_id,
                candidate.model_dump(mode="python"),
                command.evidence,
                heads,
            )
        )
    return tuple(staged)
