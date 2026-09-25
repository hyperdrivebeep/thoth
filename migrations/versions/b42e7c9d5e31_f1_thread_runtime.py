"""F1 thread activity checkpoint and input queue

Revision ID: b42e7c9d5e31
Revises: a31f6b8c4d20
Create Date: 2026-08-31 00:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b42e7c9d5e31"
down_revision: str | None = "a31f6b8c4d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("threads") as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("scope_json", sa.Text(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("parent_thread_id", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("fork_origin", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.String(length=32),
                nullable=False,
                server_default="1970-01-01T00:00:00Z",
            )
        )
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.String(length=32),
                nullable=False,
                server_default="1970-01-01T00:00:00Z",
            )
        )
    op.create_table(
        "thread_activities",
        sa.Column("activity_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("thread_id", sa.String(length=160), nullable=False),
        sa.Column("cycle_id", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(length=160), nullable=False),
        sa.Column("activity_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("activity_id"),
        sa.UniqueConstraint("activity_digest"),
    )
    op.create_index("ix_thread_activities_project_id", "thread_activities", ["project_id"])
    op.create_index("ix_thread_activities_thread_id", "thread_activities", ["thread_id"])
    op.create_table(
        "thread_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("thread_id", sa.String(length=160), nullable=False),
        sa.Column("cycle_id", sa.String(length=160), nullable=False),
        sa.Column("head_set_digest", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("checkpoint_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("checkpoint_id"),
        sa.UniqueConstraint("checkpoint_digest"),
    )
    op.create_index("ix_thread_checkpoints_project_id", "thread_checkpoints", ["project_id"])
    op.create_index("ix_thread_checkpoints_thread_id", "thread_checkpoints", ["thread_id"])
    op.create_table(
        "thread_inputs",
        sa.Column("input_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("thread_id", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("input_id"),
        sa.UniqueConstraint("thread_id", "ordinal", name="uq_thread_input_ordinal"),
    )
    op.create_index("ix_thread_inputs_project_id", "thread_inputs", ["project_id"])
    op.create_index("ix_thread_inputs_thread_id", "thread_inputs", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_thread_inputs_thread_id", table_name="thread_inputs")
    op.drop_index("ix_thread_inputs_project_id", table_name="thread_inputs")
    op.drop_table("thread_inputs")
    op.drop_index("ix_thread_checkpoints_thread_id", table_name="thread_checkpoints")
    op.drop_index("ix_thread_checkpoints_project_id", table_name="thread_checkpoints")
    op.drop_table("thread_checkpoints")
    op.drop_index("ix_thread_activities_thread_id", table_name="thread_activities")
    op.drop_index("ix_thread_activities_project_id", table_name="thread_activities")
    op.drop_table("thread_activities")
    with op.batch_alter_table("threads") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("revision")
        batch_op.drop_column("fork_origin")
        batch_op.drop_column("parent_thread_id")
        batch_op.drop_column("scope_json")
        batch_op.drop_column("display_name")
