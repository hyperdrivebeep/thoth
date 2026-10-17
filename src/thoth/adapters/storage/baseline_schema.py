"""Baseline table registration on the caller-owned metadata."""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table
from sqlalchemy.types import Integer, String, Text


def define_baseline_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table]:
    project_head_sets = Table(
        "project_head_sets",
        metadata,
        Column("project_id", String(160), primary_key=True),
        Column("revision", Integer, nullable=False),
        Column("head_set_digest", String(64), nullable=False),
        Column("content_json", Text, nullable=False),
    )

    baseline_candidates = Table(
        "baseline_candidates",
        metadata,
        Column("candidate_id", String(200), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("scope", String(80), nullable=False),
        Column("state", String(60), nullable=False),
        Column("candidate_digest", String(64), nullable=False, unique=True),
        Column("content_json", Text, nullable=False),
    )

    baseline_sets = Table(
        "baseline_sets",
        metadata,
        Column("baseline_set_id", String(200), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("scope", String(80), nullable=False),
        Column("lifecycle", String(40), nullable=False),
        Column("baseline_set_digest", String(64), nullable=False, unique=True),
        Column("content_json", Text, nullable=False),
    )

    baseline_decisions = Table(
        "baseline_decisions",
        metadata,
        Column("decision_id", String(200), primary_key=True),
        Column("project_id", String(160), nullable=False, index=True),
        Column("candidate_id", String(200), nullable=False),
        Column("decision", String(20), nullable=False),
        Column("decision_digest", String(64), nullable=False, unique=True),
        Column("content_json", Text, nullable=False),
    )
    return project_head_sets, baseline_candidates, baseline_sets, baseline_decisions
