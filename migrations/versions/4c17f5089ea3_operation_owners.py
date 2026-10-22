"""Record internal operation process ownership without changing existing rows."""

import sqlalchemy as sa
from alembic import op

revision = "4c17f5089ea3"
down_revision = "3b06e4f87d92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_owners",
        sa.Column("operation_id", sa.String(160), primary_key=True),
        sa.Column("worker_id", sa.String(160), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column("process_marker", sa.String(160), nullable=False),
        sa.Column("host_token", sa.String(160), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("operation_owners")
