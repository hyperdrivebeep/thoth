"""Rebuildable research identity index; semantic snapshots remain canonical and immutable."""

from sqlalchemy import Column, Index, String, Table, Text

from thoth.adapters.storage.schema import metadata

research_identities = Table(
    "research_identities",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("owner_revision", String(64), primary_key=True),
    Column("entity_id", String(200), nullable=False),
    Column("aggregate_kind", String(40), nullable=False),
    Column("object_id", String(200), nullable=True),
    Column("schema_family", String(80), nullable=False),
    Column("snapshot_id", String(200), nullable=False),
    Column("content_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
)
Index(
    "ix_research_identity_entity",
    research_identities.c.entity_id,
    research_identities.c.aggregate_kind,
)
