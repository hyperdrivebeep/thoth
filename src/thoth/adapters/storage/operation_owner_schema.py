"""Internal process ownership, separate from the public operation columns."""

from sqlalchemy import Column, Integer, String, Table

from thoth.adapters.storage.schema import metadata

operation_owners = Table(
    "operation_owners",
    metadata,
    Column("operation_id", String(160), primary_key=True),
    Column("worker_id", String(160), nullable=False),
    Column("process_id", Integer, nullable=False),
    Column("process_marker", String(160), nullable=False),
    Column("host_token", String(160), nullable=False),
)
