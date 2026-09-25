"""Project history includes branches; row positions stay an adapter detail."""

from hashlib import sha256

from sqlalchemy import Engine, Integer, func, literal, literal_column, select, tuple_, union_all

from thoth.adapters.storage.schema import (
    artifact_versions,
    artifacts,
    evidence_spans,
    memory_revision_ledger,
    semantic_revisions,
)
from thoth.adapters.storage.transaction import read_connection
from thoth.domain.memory import FullMemoryRevision
from thoth.domain.research_basis import SourceBasisRef
from thoth.ports.research_history import HistoryRow, HistoryWatermark


class SqliteResearchHistoryReader:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def watermark(self, project_id: str) -> HistoryWatermark:
        with read_connection(self._engine) as connection:
            values = [
                int(
                    connection.execute(
                        select(func.coalesce(func.max(literal_column("rowid", Integer)), 0))
                        .select_from(table)
                        .where(table.c.project_id == project_id)
                    ).scalar_one()
                )
                for table in (semantic_revisions, memory_revision_ledger)
            ]
        return HistoryWatermark(*values)

    def page(
        self,
        project_id: str,
        watermark: HistoryWatermark,
        after: tuple[str, str, str] | None,
        limit: int,
    ) -> tuple[HistoryRow, ...]:
        revisions = select(
            semantic_revisions.c.created_at.label("occurred_at"),
            literal("SEMANTIC_REVISION").label("owner_kind"),
            semantic_revisions.c.revision_id.label("immutable_id"),
            semantic_revisions.c.revision_digest,
        ).where(
            semantic_revisions.c.project_id == project_id,
            literal_column("semantic_revisions.rowid") <= watermark.revisions,
        )
        memories = select(
            func.json_extract(memory_revision_ledger.c.content_json, "$.created_at").label(
                "occurred_at"
            ),
            literal("FULL_MEMORY").label("owner_kind"),
            memory_revision_ledger.c.memory_revision_id.label("immutable_id"),
            memory_revision_ledger.c.revision_digest,
        ).where(
            memory_revision_ledger.c.project_id == project_id,
            literal_column("memory_revision_ledger.rowid") <= watermark.memories,
        )
        rows = union_all(revisions, memories).subquery()
        statement = select(rows)
        keys = (rows.c.occurred_at, rows.c.owner_kind, rows.c.immutable_id)
        if after is not None:
            statement = statement.where(tuple_(*keys) > after)
        statement = statement.order_by(*keys).limit(limit)
        with read_connection(self._engine) as connection:
            result = connection.execute(statement).mappings().all()
        return tuple(HistoryRow(**dict(row)) for row in result)

    def read_memory(self, project_id: str, digest: str) -> FullMemoryRevision | None:
        with read_connection(self._engine) as connection:
            raw = connection.execute(
                select(memory_revision_ledger.c.content_json).where(
                    memory_revision_ledger.c.project_id == project_id,
                    memory_revision_ledger.c.revision_digest == digest,
                )
            ).scalar_one_or_none()
        return None if raw is None else FullMemoryRevision.model_validate_json(raw)

    def read_immutable_span_basis(self, project_id: str, span_id: str) -> SourceBasisRef | None:
        # Identity/text/hash/version are INSERT-only; update_evidence changes mutable flags only.
        with read_connection(self._engine) as connection:
            row = (
                connection.execute(
                    select(evidence_spans)
                    .join(
                        artifact_versions,
                        (
                            artifact_versions.c.source_version_id
                            == evidence_spans.c.source_version_id
                        )
                        & (artifact_versions.c.artifact_id == evidence_spans.c.artifact_id),
                    )
                    .join(artifacts, artifacts.c.artifact_id == evidence_spans.c.artifact_id)
                    .where(
                        evidence_spans.c.project_id == project_id,
                        artifacts.c.project_id == project_id,
                        evidence_spans.c.span_id == span_id,
                    )
                )
                .mappings()
                .first()
            )
        if row is None or sha256(str(row["exact_text"]).encode()).hexdigest() != str(
            row["text_sha256"]
        ):
            return None
        return SourceBasisRef(
            artifact_id=str(row["artifact_id"]),
            source_version_id=str(row["source_version_id"]),
            span_id=str(row["span_id"]),
            text_sha256=str(row["text_sha256"]),
        )
