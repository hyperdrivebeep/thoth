"""Read one exact source-version catalog without per-span or correction queries."""

from sqlalchemy import Engine, select

from thoth.adapters.storage.artifact_rows import evidence_from_row
from thoth.adapters.storage.transaction import read_connection
from thoth.domain.evidence import EvidenceSpan


def read_evidence_version(
    engine: Engine, project: str, artifact: str, version: str
) -> tuple[EvidenceSpan, ...]:
    from thoth.adapters.storage.schema import evidence_spans

    with read_connection(engine) as connection:
        rows = (
            connection.execute(
                select(evidence_spans)
                .where(
                    evidence_spans.c.project_id == project,
                    evidence_spans.c.artifact_id == artifact,
                    evidence_spans.c.source_version_id == version,
                )
                .order_by(evidence_spans.c.span_id)
            )
            .mappings()
            .all()
        )
    return tuple(evidence_from_row(row) for row in rows)
