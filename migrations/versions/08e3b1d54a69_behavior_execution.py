"""Append-only behavior exposure and approved baseline history."""

import sqlalchemy as sa
from alembic import op

revision = "08e3b1d54a69"
down_revision = "f72c96e43a58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "behavior_exposure_history",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("exposure_id", sa.String(200), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("record_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "behavior_baseline_history",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("scope_digest", sa.String(64), primary_key=True),
        sa.Column("component", sa.String(64), primary_key=True),
        sa.Column("environment", sa.String(160), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("receipt_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("behavior_baseline_history")
    op.drop_table("behavior_exposure_history")
