from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, select
from tests.unit.test_research_projection import make_hypothesis

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.alembic_migrations import AlembicSchemaMigrator
from thoth.adapters.storage.research_identity import SqliteResearchIdentityStore
from thoth.adapters.storage.schema import entity_snapshots
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.application.services.research_record_persistence import rebuild_identity_index
from thoth.application.services.revision_service import RevisionCommitService
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ActorKind, CausalLocus, EntityType, PortfolioStatus
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.research_identity import ResearchFamily
from thoth.domain.revision import (
    EntitySnapshot,
    ImpactPropagationPlan,
    RevisionChangeSet,
    SemanticRevision,
    StagedRevision,
)

ROOT = Path(__file__).resolve().parents[2]


def seed_legacy(
    ledger: SqliteLedger, project_id: str, identifier: str, content: dict[str, object]
) -> SemanticRevision:
    actor = ActorRef(actor_id="actor:legacy", kind=ActorKind.AGENT, role="fixture")
    clock, ids = SystemClock(), UuidIdGenerator()
    snapshot = EntitySnapshot(
        snapshot_id=ids.new("snapshot"),
        project_id=project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=identifier,
        schema_version="1.0.0",
        content=content,
        content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
    )
    revision = SemanticRevision(
        revision_id=ids.new("revision"),
        project_id=project_id,
        entity_type=EntityType.HYPOTHESIS,
        entity_id=identifier,
        snapshot_id=snapshot.snapshot_id,
        parent_revision_digests=(),
        actor=actor,
        reason="legacy fixture",
        evidence_refs=(),
        affected_refs=(),
        revision_digest=domain_digest("LEGACY_FIXTURE", "1.0.0", canonical_payload(content)),
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    RevisionCommitService(ledger, clock, ids, policy_version="fixture").commit(
        RevisionChangeSet(
            changeset_id=ids.new("changeset"),
            project_id=project_id,
            expected_heads={},
            staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
            impact_plan=ImpactPropagationPlan(),
            actor=actor,
            reason="legacy fixture",
        )
    )
    return revision


def test_upgrade_indexes_known_and_unknown_legacy_without_rewriting_snapshots(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option(
        "sqlalchemy.url",
        URL.create("sqlite+pysqlite", database=str(database)).render_as_string(hide_password=False),
    )
    command.upgrade(config, "f7a69b2345cd")
    ledger = SqliteLedger(database)
    ledger.initialize()
    actor = ActorRef(actor_id="actor:legacy", kind=ActorKind.AGENT, role="fixture")
    clock, ids = SystemClock(), UuidIdGenerator()
    candidate = HypothesisPortfolio(
        portfolio_id="portfolio:legacy",
        object_id="object:identity",
        hypotheses=(
            make_hypothesis("hypothesis:one", CausalLocus.INPUT_MATERIAL_DATA),
            make_hypothesis("hypothesis:two", CausalLocus.METHOD_DESIGN_IMPLEMENTATION),
        ),
        status=PortfolioStatus.TESTABLE,
        generated_from_head_set="0" * 64,
    )
    legacy = candidate.model_dump(mode="json")
    for child in legacy["hypotheses"]:
        child.pop("primary_intent", None)
    digests: list[str] = []
    cases: tuple[tuple[str, dict[str, object]], ...] = (
        ("portfolio:legacy", legacy),
        ("opaque:legacy", {"unrecognized": True}),
    )
    for identifier, content in cases:
        snapshot = EntitySnapshot(
            snapshot_id=ids.new("snapshot"),
            project_id="project:legacy",
            entity_type=EntityType.HYPOTHESIS,
            entity_id=identifier,
            schema_version="1.0.0",
            content=content,
            content_digest=domain_digest("SNAPSHOT", "1.0.0", canonical_payload(content)),
        )
        revision = SemanticRevision(
            revision_id=ids.new("revision"),
            project_id="project:legacy",
            entity_type=EntityType.HYPOTHESIS,
            entity_id=identifier,
            snapshot_id=snapshot.snapshot_id,
            parent_revision_digests=(),
            actor=actor,
            reason="legacy fixture",
            evidence_refs=(),
            affected_refs=(),
            revision_digest=domain_digest("LEGACY_FIXTURE", "1.0.0", canonical_payload(content)),
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        RevisionCommitService(ledger, clock, ids, policy_version="fixture").commit(
            RevisionChangeSet(
                changeset_id=ids.new("changeset"),
                project_id="project:legacy",
                expected_heads={},
                staged_revisions=(StagedRevision(snapshot=snapshot, revision=revision),),
                impact_plan=ImpactPropagationPlan(),
                actor=actor,
                reason="legacy fixture",
            )
        )
        digests.append(revision.revision_digest)
    with ledger.engine.connect() as connection:
        before = tuple(
            connection.execute(
                select(entity_snapshots).order_by(entity_snapshots.c.snapshot_id)
            ).all()
        )
    result = AlembicSchemaMigrator(database).upgrade_head()
    assert result.before_heads == ("f7a69b2345cd",)
    assert result.target_head == "2af5d3e76c81"
    index = SqliteResearchIdentityStore(ledger.engine, ledger)
    rebuild_identity_index(ledger, index)
    rebuild_identity_index(ledger, index)
    known, unknown = (index.read("project:legacy", digest) for digest in digests)
    assert known is not None and known.schema_family == ResearchFamily.LEGACY_HYPOTHESIS_PORTFOLIO
    assert known.embedded_entity_ids == ("hypothesis:one", "hypothesis:two")
    assert index.find_entity("hypothesis:one", EntityType.HYPOTHESIS) == (known,)
    assert unknown is not None and unknown.schema_family == ResearchFamily.UNRESOLVED
    assert unknown.object_id is None
    assert index.unindexed_revisions() == ()
    with ledger.engine.connect() as connection:
        after = tuple(
            connection.execute(
                select(entity_snapshots).order_by(entity_snapshots.c.snapshot_id)
            ).all()
        )
    assert before == after
    assert json.loads(str(after[0].content_json))
    ledger.close()
