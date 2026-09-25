from sqlalchemy import Column, Integer, String, Table, Text

from thoth.adapters.storage.schema import metadata

behavior_exposure_history = Table(
    "behavior_exposure_history",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("exposure_id", String(200), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("scope_digest", String(64), nullable=False),
    Column("stage", String(32), nullable=False),
    Column("state", String(32), nullable=False),
    Column("record_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
)

behavior_baseline_history = Table(
    "behavior_baseline_history",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("scope_digest", String(64), primary_key=True),
    Column("component", String(64), primary_key=True),
    Column("environment", String(160), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("content_digest", String(64), nullable=False),
    Column("receipt_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
)
