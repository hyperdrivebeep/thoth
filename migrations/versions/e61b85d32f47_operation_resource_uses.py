"""Bind new operation results to their resource uses; old payloads remain untouched."""

import sqlalchemy as sa
from alembic import op

revision = "e61b85d32f47"
down_revision = "d50a74c21e36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_resource_bindings",
        sa.Column("operation_id", sa.String(200), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("operation_resource_bindings")
