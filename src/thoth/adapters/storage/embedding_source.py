"""Source-backed identity checks for the derived embedding projection."""

from collections.abc import Callable

from sqlalchemy import Connection, Integer, literal_column, select, update

from thoth.adapters.storage import schema
from thoth.adapters.storage.embedding_schema import embedding_configs, embedding_jobs
from thoth.domain.artifact import StructuralDocument
from thoth.domain.embedding_index import (
    EmbeddingIndexConfig,
    EmbeddingIntent,
    source_refs,
)
from thoth.domain.evidence import EvidenceSpan

type IngestionProjection = Callable[
    [Connection, StructuralDocument, str, tuple[EvidenceSpan, ...]], None
]
type BindingProjection = Callable[[Connection, str, str], None]


def configuration(connection: Connection, project: str) -> EmbeddingIndexConfig:
    row = connection.execute(
        select(embedding_configs.c.content_json).where(embedding_configs.c.project_id == project)
    ).first()
    return (
        EmbeddingIndexConfig(project_id=project, actor_ref="local:operator")
        if row is None
        else EmbeddingIndexConfig.model_validate_json(row[0])
    )


def stage(connection: Connection, intent: EmbeddingIntent) -> str:
    from sqlalchemy.dialects.sqlite import insert

    if not configuration(connection, intent.project_id).enabled:
        return ""
    connection.execute(
        insert(embedding_jobs)
        .values(
            job_id=intent.digest,
            project_id=intent.project_id,
            artifact_id=intent.artifact_id,
            source_version_id=intent.source_version_id,
            intent_json=intent.model_dump_json(),
            state="PENDING",
            generation=1,
        )
        .on_conflict_do_nothing(index_elements=["job_id"])
    )
    return intent.digest


def project_ingestion(
    connection: Connection,
    document: StructuralDocument,
    version: str,
    spans: tuple[EvidenceSpan, ...],
) -> None:
    project = document.artifact.project_id
    config = configuration(connection, project)
    if not config.enabled:
        return
    if any(
        (s.project_id, s.artifact_id, s.source_version_id)
        != (project, document.artifact.artifact_id, version)
        for s in spans
    ):
        raise ValueError("EMBEDDING_INGESTION_BINDING_MISMATCH")
    invalidate(connection, project, document.artifact.artifact_id)
    stage(
        connection,
        EmbeddingIntent(
            project_id=project,
            artifact_id=document.artifact.artifact_id,
            source_version_id=version,
            source_sha256=document.artifact.byte_sha256,
            refs=source_refs(spans),
            model=config.model,
            config_generation=config.generation,
        ),
    )


def invalidate(connection: Connection, project: str, artifact: str) -> None:
    connection.execute(
        update(embedding_jobs)
        .where(
            embedding_jobs.c.project_id == project,
            embedding_jobs.c.artifact_id == artifact,
            embedding_jobs.c.state.in_(("PENDING", "RUNNING", "READY")),
        )
        .values(
            state="STALE",
            generation=embedding_jobs.c.generation + 1,
            owner=None,
            lease_until_ms=None,
            manifest_digest=None,
            reason="SOURCE_INVALIDATED",
        )
    )


def binding_changed(connection: Connection, project: str, artifact: str) -> None:
    active = connection.execute(
        select(schema.source_bindings.c.binding_id)
        .where(
            schema.source_bindings.c.project_id == project,
            schema.source_bindings.c.artifact_id == artifact,
            schema.source_bindings.c.state == "ACTIVE",
        )
        .limit(1)
    ).first()
    if active is None:
        invalidate(connection, project, artifact)


def header_matches(connection: Connection, intent: EmbeddingIntent) -> bool:
    config = configuration(connection, intent.project_id)
    if (
        not config.enabled
        or config.generation != intent.config_generation
        or config.model != intent.model
    ):
        return False
    active = connection.execute(
        select(schema.source_bindings.c.binding_id)
        .where(
            schema.source_bindings.c.project_id == intent.project_id,
            schema.source_bindings.c.artifact_id == intent.artifact_id,
            schema.source_bindings.c.state == "ACTIVE",
        )
        .limit(1)
    ).first()
    if active is None:
        return False
    # Same rule for replacement even when observation timestamps are equal: insertion identity.
    version = connection.execute(
        select(schema.artifact_versions.c.source_version_id, schema.artifact_versions.c.byte_sha256)
        .join(
            schema.artifacts,
            schema.artifacts.c.artifact_id == schema.artifact_versions.c.artifact_id,
        )
        .where(
            schema.artifacts.c.project_id == intent.project_id,
            schema.artifacts.c.artifact_id == intent.artifact_id,
        )
        .order_by(literal_column("artifact_versions.rowid", Integer).desc())
        .limit(1)
    ).first()
    return version is not None and tuple(version) == (
        intent.source_version_id,
        intent.source_sha256,
    )


def matches(connection: Connection, intent: EmbeddingIntent) -> bool:
    if not header_matches(connection, intent):
        return False
    from thoth.adapters.storage.artifact_rows import evidence_from_row

    rows = (
        connection.execute(
            select(schema.evidence_spans)
            .where(
                schema.evidence_spans.c.project_id == intent.project_id,
                schema.evidence_spans.c.artifact_id == intent.artifact_id,
                schema.evidence_spans.c.source_version_id == intent.source_version_id,
            )
            .order_by(schema.evidence_spans.c.span_id)
        )
        .mappings()
        .all()
    )
    actual = source_refs(tuple(evidence_from_row(r) for r in rows))
    return actual == intent.refs
