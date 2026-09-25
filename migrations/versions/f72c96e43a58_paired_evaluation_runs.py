"""Persist measured evaluation pairs and bounded exposure reservations."""

import sqlalchemy as sa
from alembic import op

revision = "f72c96e43a58"
down_revision = "e61b85d32f47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("pair_id", sa.String(200), primary_key=True),
        sa.Column("dedupe_digest", sa.String(64), nullable=False),
        sa.Column("plan_id", sa.String(200), nullable=False),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        sa.Column("fixture_digest", sa.String(64), nullable=False),
        sa.Column("scorer_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("record_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("project_id", "dedupe_digest", name="uq_evaluation_request"),
    )


def downgrade() -> None:
    op.drop_table("evaluation_runs")
