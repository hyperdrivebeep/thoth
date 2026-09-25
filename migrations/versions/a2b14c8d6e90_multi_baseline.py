"""P2 normal-loop multi-baseline integration

Revision ID: a2b14c8d6e90
Revises: 9d12a7c5e3b8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b14c8d6e90"
down_revision: str | None = "9d12a7c5e3b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_head_sets",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("head_set_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )
    for name, id_name, digest_name, extra in (
        (
            "baseline_candidates",
            "candidate_id",
            "candidate_digest",
            (
                sa.Column("scope", sa.String(80), nullable=False),
                sa.Column("state", sa.String(60), nullable=False),
            ),
        ),
        (
            "baseline_sets",
            "baseline_set_id",
            "baseline_set_digest",
            (
                sa.Column("scope", sa.String(80), nullable=False),
                sa.Column("lifecycle", sa.String(40), nullable=False),
            ),
        ),
        (
            "baseline_decisions",
            "decision_id",
            "decision_digest",
            (
                sa.Column("candidate_id", sa.String(200), nullable=False),
                sa.Column("decision", sa.String(20), nullable=False),
            ),
        ),
    ):
        op.create_table(
            name,
            sa.Column(id_name, sa.String(200), primary_key=True),
            sa.Column("project_id", sa.String(160), nullable=False),
            *extra,
            sa.Column(digest_name, sa.String(64), nullable=False, unique=True),
            sa.Column("content_json", sa.Text(), nullable=False),
        )
        op.create_index(f"ix_{name}_project_id", name, ["project_id"])


def downgrade() -> None:
    for name in ("baseline_decisions", "baseline_sets", "baseline_candidates"):
        op.drop_index(f"ix_{name}_project_id", table_name=name)
        op.drop_table(name)
    op.drop_table("project_head_sets")
