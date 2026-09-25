"""F2 decision object profiles candidates relations attention and audit

Revision ID: f86cbed19265
Revises: e75badc08154
Create Date: 2026-08-31 05:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f86cbed19265"
down_revision: str | None = "e75badc08154"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "object_profiles",
        sa.Column("profile_ref", sa.String(160), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("profile_digest", sa.String(64), nullable=False, unique=True),
    )
    for table, id_column, columns in (
        (
            "object_candidates",
            "candidate_id",
            (
                ("project_id", False),
                ("thread_id", False),
                ("candidate_digest", False),
                ("created_at", False),
            ),
        ),
        (
            "decision_objects",
            "object_revision_id",
            (
                ("object_id", False),
                ("project_id", False),
                ("thread_id", False),
                ("revision_digest", False),
                ("supersedes_revision_digest", True),
                ("created_at", False),
            ),
        ),
        (
            "object_relations",
            "relation_revision_id",
            (
                ("relation_id", False),
                ("project_id", False),
                ("source_object_id", False),
                ("revision_digest", False),
                ("created_at", False),
            ),
        ),
        (
            "object_attention",
            "attention_revision_id",
            (
                ("attention_id", False),
                ("project_id", False),
                ("object_id", False),
                ("revision_digest", False),
                ("created_at", False),
            ),
        ),
    ):
        generated = [sa.Column(id_column, sa.String(160), primary_key=True)]
        for name, nullable in columns:
            generated.append(sa.Column(name, sa.String(160), nullable=nullable))
        generated.insert(-1, sa.Column("content_json", sa.Text(), nullable=False))
        op.create_table(table, *generated)
        for name, _nullable in columns:
            if name in {
                "project_id",
                "thread_id",
                "object_id",
                "relation_id",
                "source_object_id",
                "attention_id",
            }:
                op.create_index(f"ix_{table}_{name}", table, [name])
    op.create_table(
        "object_audit",
        sa.Column("audit_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("object_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_object_audit_project_id", "object_audit", ["project_id"])
    op.create_index("ix_object_audit_object_id", "object_audit", ["object_id"])


def downgrade() -> None:
    op.drop_index("ix_object_audit_object_id", table_name="object_audit")
    op.drop_index("ix_object_audit_project_id", table_name="object_audit")
    op.drop_table("object_audit")
    indexes = {
        "object_attention": ("attention_id", "project_id", "object_id"),
        "object_relations": ("relation_id", "project_id", "source_object_id"),
        "decision_objects": ("object_id", "project_id", "thread_id"),
        "object_candidates": ("project_id", "thread_id"),
    }
    for table, columns in indexes.items():
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_table(table)
    op.drop_table("object_profiles")
