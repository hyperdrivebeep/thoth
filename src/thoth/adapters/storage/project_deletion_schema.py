"""Record kept for a permanently deleted project: it answers repeats and retires the id."""

from sqlalchemy import Column, String, Table, Text

from thoth.adapters.storage.schema import metadata

project_deletions = Table(
    "project_deletions",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("name_sha256", String(64), nullable=False),
    Column("deleted_at", String(32), nullable=False),
    Column("cleanup_json", Text, nullable=False),
)
