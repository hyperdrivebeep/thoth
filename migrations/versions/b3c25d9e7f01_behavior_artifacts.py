"""P6 versioned local behavior artifacts

Revision ID: b3c25d9e7f01
Revises: a2b14c8d6e90
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c25d9e7f01"
down_revision: str | None = "a2b14c8d6e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "behavior_artifacts",
        sa.Column("artifact_id", sa.String(200), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "kind",
            "content_digest",
            name="uq_behavior_artifact_content",
        ),
    )
    op.create_index("ix_behavior_artifacts_project_id", "behavior_artifacts", ["project_id"])
    op.create_table(
        "behavior_registry",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("kind", sa.String(80), primary_key=True),
        sa.Column("active_digest", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("behavior_registry")
    op.drop_index("ix_behavior_artifacts_project_id", table_name="behavior_artifacts")
    op.drop_table("behavior_artifacts")
