"""operation events and checkpoints

Revision ID: f9a43b25d201
Revises: e311a930d5f4
Create Date: 2026-08-30 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9a43b25d201"
down_revision: str | None = "e311a930d5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=160), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("previous_event_digest", sa.String(length=64), nullable=True),
        sa.Column("event_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.operation_id"]),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("event_digest"),
    )
    op.create_index("ix_events_project_id", "events", ["project_id"])
    op.create_table(
        "checkpoints",
        sa.Column("checkpoint_id", sa.String(length=160), nullable=False),
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("checkpoint_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.operation_id"]),
        sa.PrimaryKeyConstraint("checkpoint_id"),
        sa.UniqueConstraint("checkpoint_digest"),
    )


def downgrade() -> None:
    op.drop_table("checkpoints")
    op.drop_index("ix_events_project_id", table_name="events")
    op.drop_table("events")
