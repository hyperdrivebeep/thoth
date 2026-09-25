"""F4 Outcome profile series assessment attribution changeset impact and audit

Revision ID: 3dca0215d6a9
Revises: 2cb9e104c598
Create Date: 2026-08-31 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3dca0215d6a9"
down_revision: str | None = "2cb9e104c598"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table(name: str, primary: str, indexed: tuple[str, ...], digest: str) -> None:
    columns: list[sa.Column[object]] = [sa.Column(primary, sa.String(160), primary_key=True)]
    columns.extend(sa.Column(column, sa.String(160), nullable=False) for column in indexed)
    columns.extend(
        (
            sa.Column("content_json", sa.Text(), nullable=False),
            sa.Column(digest, sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.String(32), nullable=False),
        )
    )
    op.create_table(name, *columns)
    for column in indexed:
        op.create_index(f"ix_{name}_{column}", name, [column])


def upgrade() -> None:
    op.create_table(
        "outcome_profiles",
        sa.Column("profile_ref", sa.String(160), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("profile_digest", sa.String(64), nullable=False, unique=True),
    )
    _table(
        "outcome_series",
        "series_revision_id",
        ("outcome_series_id", "project_id", "object_id"),
        "revision_digest",
    )
    _table(
        "outcome_assessments",
        "assessment_revision_id",
        ("outcome_assessment_id", "project_id", "outcome_series_id", "object_id"),
        "revision_digest",
    )
    _table(
        "outcome_attributions",
        "attribution_assessment_id",
        ("project_id", "outcome_assessment_id"),
        "attribution_digest",
    )
    _table(
        "outcome_change_sets",
        "outcome_change_set_id",
        ("project_id", "outcome_assessment_id"),
        "change_set_digest",
    )
    _table(
        "outcome_impacts",
        "impact_assessment_id",
        ("project_id", "outcome_series_id"),
        "impact_digest",
    )
    op.create_table(
        "outcome_audit",
        sa.Column("audit_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("subject_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_outcome_audit_project_id", "outcome_audit", ["project_id"])
    op.create_index("ix_outcome_audit_subject_id", "outcome_audit", ["subject_id"])


def downgrade() -> None:
    op.drop_index("ix_outcome_audit_subject_id", table_name="outcome_audit")
    op.drop_index("ix_outcome_audit_project_id", table_name="outcome_audit")
    op.drop_table("outcome_audit")
    definitions = (
        ("outcome_impacts", ("project_id", "outcome_series_id")),
        ("outcome_change_sets", ("project_id", "outcome_assessment_id")),
        ("outcome_attributions", ("project_id", "outcome_assessment_id")),
        (
            "outcome_assessments",
            ("outcome_assessment_id", "project_id", "outcome_series_id", "object_id"),
        ),
        ("outcome_series", ("outcome_series_id", "project_id", "object_id")),
    )
    for table, columns in definitions:
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
    op.drop_table("outcome_profiles")
