"""Add explicit scope governance; do not infer ownership or rewrite legacy source bytes."""

import sqlalchemy as sa
from alembic import op

revision = "d50a74c21e36"
down_revision = "c49f63b10d25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_scope_history",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("resource_ref", sa.String(260), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("record_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "resource_scope_heads",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("resource_ref", sa.String(260), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("record_digest", sa.String(64), nullable=False),
    )
    op.create_table(
        "resource_scope_receipts",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("receipt_id", sa.String(200), primary_key=True),
        sa.Column("resource_ref", sa.String(260), nullable=False),
        sa.Column("receipt_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("resource_scope_receipts")
    op.drop_table("resource_scope_heads")
    op.drop_table("resource_scope_history")
