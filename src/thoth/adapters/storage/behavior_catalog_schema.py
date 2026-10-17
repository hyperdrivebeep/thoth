"""Behavior catalog table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table, UniqueConstraint
from sqlalchemy.types import Integer, String, Text


def define_behavior_catalog_tables(metadata: MetaData) -> tuple[Table, Table]:
    behavior_artifacts = Table(
        "behavior_artifacts",
        metadata,
        Column("artifact_id", String(200), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("kind", String(80), nullable=False),
        Column("state", String(40), nullable=False),
        Column("content_digest", String(64), nullable=False),
        Column("content_json", Text, nullable=False),
        UniqueConstraint(
            "project_id",
            "kind",
            "content_digest",
            name="uq_behavior_artifact_content",
        ),
    )

    behavior_registry = Table(
        "behavior_registry",
        metadata,
        Column("project_id", String(160), primary_key=True),
        Column("kind", String(80), primary_key=True),
        Column("active_digest", String(64), nullable=False),
        Column("revision", Integer, nullable=False),
        Column("content_json", Text, nullable=False),
    )
    return behavior_artifacts, behavior_registry
