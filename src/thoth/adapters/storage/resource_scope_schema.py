"""Immutable governance scope revisions and their current query head."""

from sqlalchemy import Column, Integer, String, Table, Text

from thoth.adapters.storage.schema import metadata

resource_scope_history = Table(
    "resource_scope_history",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("resource_ref", String(260), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("record_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
)
resource_scope_heads = Table(
    "resource_scope_heads",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("resource_ref", String(260), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("record_digest", String(64), nullable=False),
)
resource_scope_receipts = Table(
    "resource_scope_receipts",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("receipt_id", String(200), primary_key=True),
    Column("resource_ref", String(260), nullable=False),
    Column("receipt_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
)

operation_resource_bindings = Table(
    "operation_resource_bindings",
    metadata,
    Column("operation_id", String(200), primary_key=True),
    Column("project_id", String(160), nullable=False),
    Column("binding_digest", String(64), nullable=False),
    Column("content_json", Text, nullable=False),
)
