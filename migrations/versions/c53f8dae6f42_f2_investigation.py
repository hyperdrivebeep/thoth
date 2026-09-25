"""F2 bounded investigation state and audit ledger

Revision ID: c53f8dae6f42
Revises: b42e7c9d5e31
Create Date: 2026-08-31 01:45:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c53f8dae6f42"
down_revision: str | None = "b42e7c9d5e31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investigations",
        sa.Column("investigation_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("thread_id", sa.String(length=160), nullable=False),
        sa.Column("cycle_id", sa.String(length=160), nullable=False),
        sa.Column("parent_investigation_id", sa.String(length=160), nullable=True),
        sa.Column("trigger", sa.String(length=80), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("target_object_id", sa.String(length=160), nullable=True),
        sa.Column("target_hypothesis_id", sa.String(length=160), nullable=True),
        sa.Column("mode", sa.String(length=40), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False),
        sa.Column("required_evidence_groups_json", sa.Text(), nullable=False),
        sa.Column("query_families_json", sa.Text(), nullable=False),
        sa.Column("counter_search_policy", sa.String(length=160), nullable=False),
        sa.Column("budget", sa.Integer(), nullable=False),
        sa.Column("budget_usage", sa.Integer(), nullable=False),
        sa.Column("stop_conditions_json", sa.Text(), nullable=False),
        sa.Column("domain_state", sa.String(length=40), nullable=False),
        sa.Column("execution_state", sa.String(length=40), nullable=False),
        sa.Column("current_wave", sa.Integer(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("open_lead_count", sa.Integer(), nullable=False),
        sa.Column("claim_candidate_count", sa.Integer(), nullable=False),
        sa.Column("gap_count", sa.Integer(), nullable=False),
        sa.Column("sufficiency_json", sa.Text(), nullable=False),
        sa.Column("checkpoint_digest", sa.String(length=64), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("investigation_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("investigation_id"),
        sa.UniqueConstraint("investigation_digest"),
    )
    op.create_index("ix_investigations_project_id", "investigations", ["project_id"])
    op.create_index("ix_investigations_thread_id", "investigations", ["thread_id"])
    op.create_table(
        "investigation_audit",
        sa.Column("audit_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("investigation_id", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("event_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("audit_id"),
        sa.UniqueConstraint("event_digest"),
    )
    op.create_index("ix_investigation_audit_project_id", "investigation_audit", ["project_id"])
    op.create_index(
        "ix_investigation_audit_investigation_id",
        "investigation_audit",
        ["investigation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_investigation_audit_investigation_id", table_name="investigation_audit")
    op.drop_index("ix_investigation_audit_project_id", table_name="investigation_audit")
    op.drop_table("investigation_audit")
    op.drop_index("ix_investigations_thread_id", table_name="investigations")
    op.drop_index("ix_investigations_project_id", table_name="investigations")
    op.drop_table("investigations")
