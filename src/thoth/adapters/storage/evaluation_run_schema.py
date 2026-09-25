from sqlalchemy import Column, Integer, String, Table, Text, UniqueConstraint

from thoth.adapters.storage.schema import metadata

evaluation_runs = Table(
    "evaluation_runs",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("pair_id", String(200), primary_key=True),
    Column("dedupe_digest", String(64), nullable=False),
    Column("plan_id", String(200), nullable=False),
    Column("plan_digest", String(64), nullable=False),
    Column("fixture_digest", String(64), nullable=False),
    Column("scorer_digest", String(64), nullable=False),
    Column("state", String(32), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("record_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
    UniqueConstraint("project_id", "dedupe_digest", name="uq_evaluation_request"),
)
