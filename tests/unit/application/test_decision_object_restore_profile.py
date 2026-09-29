"""The active DecisionObjectRecord producer has one exact typed restore profile."""

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from thoth.apps.restore_profiles import restore_profiles
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.decision_object_full import DecisionObjectRecord
from thoth.domain.enums import ActorKind, EntityType
from thoth.domain.restore import RestoreError
from thoth.domain.revision import EntitySnapshot, SemanticRevision


def recorded() -> tuple[SemanticRevision, EntitySnapshot]:
    moment = datetime.now(UTC)
    record = DecisionObjectRecord(
        object_revision_id="object-revision:fixture",
        object_id="object:fixture",
        project_id="project:fixture",
        thread_id="thread:fixture",
        purpose_statement="A bounded research decision",
        problem_frame="A bounded research decision",
        focus_refs=("workstream:fixture",),
        workstream_refs=("fixture",),
        materialization_trigger="EXPLICIT_USER_REQUEST",
        trigger_evidence_refs=("span:fixture",),
        entry_criteria={"status": "PASS"},
        exit_criteria={"status": "NOT_SATISFIED"},
        actor_or_agent_ref="agent:fixture",
        revision_digest="a" * 64,
        created_at=moment,
    )
    content = record.model_dump(mode="python")
    snapshot = EntitySnapshot(
        snapshot_id="snapshot:fixture",
        project_id=record.project_id,
        entity_type=EntityType.DECISION_OBJECT,
        entity_id=record.object_id,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    revision = SemanticRevision(
        revision_id=record.object_revision_id,
        project_id=record.project_id,
        entity_type=EntityType.DECISION_OBJECT,
        entity_id=record.object_id,
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=(),
        actor=ActorRef(actor_id="agent:fixture", kind=ActorKind.AGENT, role="object-editor"),
        reason="object/materialized",
        evidence_refs=record.trigger_evidence_refs,
        affected_refs=(),
        revision_digest=record.revision_digest,
        created_at=moment,
    )
    return revision, snapshot


def with_content(snapshot: EntitySnapshot, changes: Mapping[str, object]) -> EntitySnapshot:
    content = {**snapshot.content, **changes}
    return snapshot.model_copy(
        update={
            "content": content,
            "content_digest": domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
        }
    )


def test_active_producer_codec_and_six_prior_profiles_remain_distinct() -> None:
    registry = restore_profiles()
    prior = {
        "evidence-assessment.v1",
        "hypothesis.v1",
        "hypothesis-portfolio.v1",
        "action.v1",
        "action-portfolio.v1",
        "action-plan.v1",
    }
    assert prior.issubset({profile.profile_id for profile in registry.profiles})
    revision, snapshot = recorded()
    profile, decoded = registry.resolve(revision, snapshot)
    assert profile.profile_id == "decision-object-record.v1"
    assert isinstance(decoded, DecisionObjectRecord)
    assert profile.object_id(decoded) == revision.entity_id
    assert profile.references(decoded) == ()
    assert profile.evidence_refs(decoded) == ("span:fixture",)


def test_legacy_simple_decision_shape_and_wrong_schema_are_not_aliases() -> None:
    registry = restore_profiles()
    revision, snapshot = recorded()
    legacy = {
        "object_id": revision.entity_id,
        "project_id": revision.project_id,
        "thread_id": "thread:fixture",
        "title": "Legacy",
        "problem": "Legacy",
        "object_profile": "GENERAL_RND_DECISION",
        "created_at": datetime.now(UTC),
        "schema_version": "1.0.0",
    }
    with pytest.raises(RestoreError, match="RESTORE_SCHEMA_UNSUPPORTED"):
        registry.resolve(revision, with_content(snapshot, legacy))
    with pytest.raises(RestoreError, match="RESTORE_SCHEMA_UNSUPPORTED"):
        registry.resolve(revision, snapshot.model_copy(update={"schema_version": "1.1.0"}))
    with pytest.raises(RestoreError, match="RESTORE_SCHEMA_UNSUPPORTED"):
        registry.resolve(revision, with_content(snapshot, {"schema_version": "2.0.0"}))
    with pytest.raises(RestoreError, match="RESTORE_SCHEMA_UNSUPPORTED"):
        registry.resolve(revision, with_content(snapshot, {"schema_version": None}))


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"project_id": "project:other"}, "RESTORE_TARGET_MISMATCH"),
        ({"object_id": "object:other"}, "RESTORE_TARGET_MISMATCH"),
        ({"purpose_statement": {"invalid": "shape"}}, "RESTORE_SCHEMA_UNSUPPORTED"),
    ),
)
def test_wrong_identity_or_shape_fails_closed(changes: dict[str, object], reason: str) -> None:
    revision, snapshot = recorded()
    with pytest.raises(RestoreError, match=reason):
        restore_profiles().resolve(revision, with_content(snapshot, changes))


def test_snapshot_digest_and_outer_identity_are_checked() -> None:
    revision, snapshot = recorded()
    with pytest.raises(RestoreError, match="RESTORE_CONTENT_DIGEST_MISMATCH"):
        restore_profiles().resolve(
            revision,
            snapshot.model_copy(
                update={
                    "content_digest": domain_digest(
                        "SNAPSHOT", "1.0.0", canonical_payload(snapshot.content)
                    )
                }
            ),
        )
    with pytest.raises(RestoreError, match="RESTORE_CONTENT_DIGEST_MISMATCH"):
        restore_profiles().resolve(
            revision, snapshot.model_copy(update={"content_digest": "0" * 64})
        )
    with pytest.raises(RestoreError, match="RESTORE_TARGET_MISMATCH"):
        restore_profiles().resolve(
            revision, snapshot.model_copy(update={"entity_id": "object:other"})
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"parent_object_id": "object:missing"},
        {"parent_object_id": ""},
        {"requirement_refs": ("requirement:missing",)},
        {"cutoff_ref": "cutoff:unknown"},
        {"cutoff_ref": ""},
        {"relation_refs": ("relation:missing",)},
    ),
)
def test_unproved_cross_object_references_require_review(changes: dict[str, object]) -> None:
    revision, snapshot = recorded()
    profile, decoded = restore_profiles().resolve(revision, with_content(snapshot, changes))
    assert isinstance(decoded, DecisionObjectRecord)
    with pytest.raises(RestoreError, match="RESTORE_MEMBERS_REQUIRE_REVIEW"):
        profile.references(decoded)
