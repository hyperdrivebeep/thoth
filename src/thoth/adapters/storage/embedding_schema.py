"""Derived, project-scoped embedding projection and durable work intents."""

from sqlalchemy import Column, Integer, LargeBinary, String, Table, Text

from thoth.adapters.storage.schema import metadata

embedding_configs = Table(
    "embedding_configs",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("generation", Integer, nullable=False),
    Column("enabled", Integer, nullable=False),
    Column("content_json", Text, nullable=False),
)
embedding_jobs = Table(
    "embedding_jobs",
    metadata,
    Column("job_id", String(64), primary_key=True),
    Column("project_id", String(160), nullable=False, index=True),
    Column("artifact_id", String(160), nullable=False, index=True),
    Column("source_version_id", String(160), nullable=False),
    Column("intent_json", Text, nullable=False),
    Column("state", String(20), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("owner", String(160), nullable=True),
    Column("lease_until_ms", Integer, nullable=True),
    Column("deadline_ms", Integer, nullable=True),
    Column("reason", String(100), nullable=True),
    Column("manifest_digest", String(64), nullable=True),
)
embedding_vectors = Table(
    "embedding_vectors",
    metadata,
    Column("project_id", String(160), primary_key=True),
    Column("content_key", String(64), primary_key=True),
    Column("vector_sha256", String(64), nullable=False),
    Column("data", LargeBinary, nullable=False),
)
