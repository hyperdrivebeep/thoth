"""Add ledger records from outside a running investigation, the way another user or job would."""

from __future__ import annotations

from datetime import UTC, datetime

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.application.services.revision_service import CommitResult, RevisionCommitService
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)

ACTOR = ActorRef(actor_id="human:other", kind=ActorKind.HUMAN, role="editor")


def add_revision(
    runtime: AppRuntime,
    project: str,
    entity_type: EntityType,
    entity_id: str,
    content: dict[str, object],
) -> CommitResult:
    """Commit one new revision of `entity_type:entity_id` on top of its current head."""

    ids = UuidIdGenerator()
    key = f"{entity_type.value}:{entity_id}"
    parent = runtime.ledger.read_heads(project).get(key)
    snapshot = EntitySnapshot(
        snapshot_id=ids.new("snapshot"),
        project_id=project,
        entity_type=entity_type,
        entity_id=entity_id,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    draft: dict[str, object] = {
        "revision_id": ids.new("revision"),
        "project_id": project,
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "snapshot_id": snapshot.snapshot_id,
        "parent_revision_digests": [] if parent is None else [parent],
        "actor": ACTOR,
        "reason": "test",
        "evidence_refs": [],
        "affected_refs": [],
        "created_at": datetime.now(UTC),
    }
    revision = SemanticRevision.model_validate(
        {
            **draft,
            "revision_digest": domain_digest(
                "SEMANTIC_REVISION", "1.0.0", canonical_payload(draft)
            ),
        }
    )
    commits = RevisionCommitService(
        runtime.ledger, SystemClock(), ids, policy_version="readset-test"
    )
    return commits.commit(
        RevisionChangeSet(
            changeset_id=ids.new("changeset"),
            project_id=project,
            expected_heads={} if parent is None else {key: parent},
            staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
            impact_plan=ImpactPropagationPlan(),
            actor=ACTOR,
            reason="test",
        )
    )
