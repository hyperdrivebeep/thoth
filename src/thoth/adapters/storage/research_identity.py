from __future__ import annotations

from sqlalchemy import Engine, Table, and_, func, insert, or_, select

from thoth.adapters.storage.research_schema import research_identities
from thoth.adapters.storage.schema import semantic_revisions
from thoth.adapters.storage.transaction import read_connection, write_connection
from thoth.domain.base import DomainModel
from thoth.domain.enums import EntityType
from thoth.domain.research_identity import (
    ResearchFamily,
    ResearchIdentity,
    ResearchIdentityError,
    decode_research,
)
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_identity import ResearchIdentityStorePort
from thoth.ports.resource_scope import ResourceAccessPort


class SqliteResearchIdentityStore(ResearchIdentityStorePort):
    def __init__(self, engine: Engine, ledger: LedgerPort) -> None:
        self._engine = engine
        self._ledger = ledger

    def stage_revision(self, revision: SemanticRevision) -> None:
        if revision.entity_type not in {
            EntityType.HYPOTHESIS,
            EntityType.ACTION,
            EntityType.CRITERION,
            EntityType.OUTCOME,
        }:
            return
        snapshot = self._ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None:
            raise ResearchIdentityError("RESEARCH_IDENTITY_SOURCE_MISSING")
        self.add(decode_research(revision, snapshot).identity)

    def add(self, identity: ResearchIdentity) -> None:
        with write_connection(self._engine) as connection:
            old = connection.execute(
                select(research_identities.c.content_json).where(
                    research_identities.c.project_id == identity.project_id,
                    research_identities.c.owner_revision == identity.owner_revision,
                )
            ).scalar_one_or_none()
            if old is not None:
                if ResearchIdentity.model_validate_json(str(old)) != identity:
                    raise ResearchIdentityError("RESEARCH_IDENTITY_INDEX_CONFLICT")
                return
            connection.execute(
                insert(research_identities).values(
                    **identity.model_dump(mode="json", exclude={"embedded_entity_ids"}),
                    content_json=identity.model_dump_json(),
                )
            )

    def read(self, project_id: str, owner_revision: str) -> ResearchIdentity | None:
        with read_connection(self._engine) as connection:
            raw = connection.execute(
                select(research_identities.c.content_json).where(
                    research_identities.c.project_id == project_id,
                    research_identities.c.owner_revision == owner_revision,
                )
            ).scalar_one_or_none()
        return None if raw is None else ResearchIdentity.model_validate_json(str(raw))

    def find_entity(self, entity_id: str, kind: EntityType) -> tuple[ResearchIdentity, ...]:
        members = func.json_each(
            research_identities.c.content_json, "$.embedded_entity_ids"
        ).table_valued("value")
        embedded = select(1).select_from(members).where(members.c.value == entity_id).exists()
        with read_connection(self._engine) as connection:
            rows = (
                connection.execute(
                    select(research_identities.c.content_json).where(
                        or_(research_identities.c.entity_id == entity_id, embedded),
                        research_identities.c.aggregate_kind == kind.value,
                    )
                )
                .scalars()
                .all()
            )
        return tuple(ResearchIdentity.model_validate_json(str(raw)) for raw in rows)

    def unindexed_revisions(self) -> tuple[tuple[SemanticRevision, EntitySnapshot], ...]:
        join = semantic_revisions.outerjoin(
            research_identities,
            and_(
                semantic_revisions.c.project_id == research_identities.c.project_id,
                semantic_revisions.c.revision_digest == research_identities.c.owner_revision,
            ),
        )
        with read_connection(self._engine) as connection:
            rows = connection.execute(
                select(
                    semantic_revisions.c.project_id,
                    semantic_revisions.c.revision_digest,
                )
                .select_from(join)
                .where(
                    semantic_revisions.c.entity_type.in_(
                        (
                            EntityType.HYPOTHESIS.value,
                            EntityType.ACTION.value,
                            EntityType.CRITERION.value,
                            EntityType.OUTCOME.value,
                        )
                    ),
                    research_identities.c.owner_revision.is_(None),
                )
            ).all()
        result: list[tuple[SemanticRevision, EntitySnapshot]] = []
        for row in rows:
            revision = self._ledger.read_revision_by_digest(str(row[0]), str(row[1]))
            snapshot = (
                None if revision is None else self._ledger.read_snapshot(revision.snapshot_id)
            )
            if revision is None or snapshot is None:
                raise ResearchIdentityError("RESEARCH_IDENTITY_SOURCE_MISSING")
            result.append((revision, snapshot))
        return tuple(result)


def canonical_records[T: DomainModel](
    ledger: LedgerPort,
    engine: Engine,
    kind: EntityType,
    project_id: str,
    table: Table,
    id_field: str,
    model: type[T],
    *,
    identifier: str | None = None,
    revision_digest: str | None = None,
    resource_access: ResourceAccessPort | None = None,
) -> tuple[T, ...]:
    heads = ledger.read_heads(project_id)
    if revision_digest is not None:
        digests = (revision_digest,)
    elif identifier is not None:
        selected = heads.get(f"{kind.value}:{identifier}")
        digests = () if selected is None else (selected,)
    else:
        digests = tuple(value for key, value in heads.items() if key.startswith(kind.value + ":"))
    results: list[T] = []
    for digest in digests:
        revision = ledger.read_revision_by_digest(project_id, digest)
        snapshot = None if revision is None else ledger.read_snapshot(revision.snapshot_id)
        if revision is None or snapshot is None:
            raise ResearchIdentityError("RESEARCH_IDENTITY_SOURCE_MISSING")
        if identifier is not None and revision.entity_id != identifier:
            raise ResearchIdentityError("RESEARCH_RECORD_IDENTITY_MISMATCH")
        decoded = decode_research(revision, snapshot)
        if decoded.identity.schema_family == ResearchFamily.UNRESOLVED:
            raise ResearchIdentityError("LEGACY_SCHEMA_UNRESOLVED")
        if not isinstance(decoded.record, model):
            if identifier is not None:
                raise ResearchIdentityError("LEGACY_SCHEMA_UNRESOLVED")
            continue
        if resource_access is not None:
            if identifier is not None or revision_digest is not None:
                resource_access.require_revision(project_id, digest)
            elif not resource_access.may_read_revision(project_id, digest):
                continue
        with read_connection(engine) as connection:
            raw = connection.execute(
                select(table.c.content_json).where(
                    table.c.project_id == project_id,
                    table.c[id_field] == revision.entity_id,
                    table.c.revision_digest == digest,
                )
            ).scalar_one_or_none()
        if raw is not None and model.model_validate_json(str(raw)) != decoded.record:
            raise ResearchIdentityError("RESEARCH_CANONICAL_PROJECTION_MISMATCH")
        results.append(decoded.record)
    return tuple(results)
