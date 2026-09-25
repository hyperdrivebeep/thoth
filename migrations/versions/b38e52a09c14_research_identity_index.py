"""Add a rebuildable schema-family and research identity index without rewriting snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "b38e52a09c14"
down_revision = "f7a69b2345cd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_identities",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("owner_revision", sa.String(64), primary_key=True),
        sa.Column("entity_id", sa.String(200), nullable=False),
        sa.Column("aggregate_kind", sa.String(40), nullable=False),
        sa.Column("object_id", sa.String(200), nullable=True),
        sa.Column("schema_family", sa.String(80), nullable=False),
        sa.Column("snapshot_id", sa.String(200), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_research_identity_entity", "research_identities", ["entity_id", "aggregate_kind"]
    )


def downgrade() -> None:
    op.drop_index("ix_research_identity_entity", table_name="research_identities")
    op.drop_table("research_identities")
