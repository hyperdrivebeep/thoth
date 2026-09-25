"""revision dependency and memory state

Revision ID: d4ac821bf1e2
Revises: c7e8a19fe3a1
Create Date: 2026-08-30 07:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4ac821bf1e2"
down_revision: str | None = "c7e8a19fe3a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("entity_snapshots", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_entity_snapshots_content_digest", type_="unique")
    op.create_table(
        "relations",
        sa.Column("relation_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("source_ref", sa.String(length=260), nullable=False),
        sa.Column("relation_type", sa.String(length=80), nullable=False),
        sa.Column("target_ref", sa.String(length=260), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("revision_digest", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("relation_id"),
    )
    op.create_index("ix_relations_project_id", "relations", ["project_id"])
    op.create_index("ix_relations_source_ref", "relations", ["source_ref"])
    op.create_table(
        "dependency_states",
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("entity_ref", sa.String(length=260), nullable=False),
        sa.Column("status", sa.String(length=60), nullable=False),
        sa.Column("caused_by_revision", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "entity_ref"),
    )
    op.create_table(
        "memory_records",
        sa.Column("memory_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("payload_mode", sa.String(length=60), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("owner_revision_ref", sa.String(length=160), nullable=False),
        sa.Column("source_ref", sa.String(length=260), nullable=True),
        sa.Column("assertion", sa.Text(), nullable=True),
        sa.Column("recall_eligibility", sa.String(length=60), nullable=False),
        sa.Column("lifecycle", sa.String(length=60), nullable=False),
        sa.Column("revision_digest", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("memory_id"),
    )
    op.create_index("ix_memory_records_project_id", "memory_records", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_records_project_id", table_name="memory_records")
    op.drop_table("memory_records")
    op.drop_table("dependency_states")
    op.drop_index("ix_relations_source_ref", table_name="relations")
    op.drop_index("ix_relations_project_id", table_name="relations")
    op.drop_table("relations")
    with op.batch_alter_table("entity_snapshots", recreate="always") as batch_op:
        batch_op.create_unique_constraint("uq_entity_snapshots_content_digest", ["content_digest"])
