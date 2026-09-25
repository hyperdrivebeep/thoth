from sqlalchemy import Column, Integer, String, Table, Text

from thoth.adapters.storage.schema import metadata

governance_revisions = Table(
    "governance_revisions",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("record_kind", String(32), primary_key=True),
    Column("record_id", String(200), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("record_digest", String(64), nullable=False, unique=True),
    Column("content_json", Text, nullable=False),
    Column("receipt_digest", String(64), nullable=False, unique=True),
    Column("receipt_json", Text, nullable=False),
)

governance_heads = Table(
    "governance_heads",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("record_kind", String(32), primary_key=True),
    Column("record_id", String(200), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("record_digest", String(64), nullable=False),
)
