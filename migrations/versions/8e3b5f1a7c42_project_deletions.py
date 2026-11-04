"""Record of permanently deleted projects (id, name digest, time, pending file cleanup)."""

import sqlalchemy as sa
from alembic import op

revision = "8e3b5f1a7c42"
down_revision = "5d28a619afb4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_deletions",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("name_sha256", sa.String(64), nullable=False),
        sa.Column("deleted_at", sa.String(32), nullable=False),
        sa.Column("cleanup_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("project_deletions")
