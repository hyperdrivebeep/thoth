"""Record, per stored vector, whether its passage was cut to the token cap (additive, nullable).

Existing rows keep NULL: they were made before the cap was recorded. Nothing is rewritten.
"""

import sqlalchemy as sa
from alembic import op

revision = "9f4c2d7e1a35"
down_revision = "8e3b5f1a7c42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("embedding_vectors", sa.Column("truncated", sa.Integer(), nullable=True))
    op.add_column("embedding_vectors", sa.Column("source_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("embedding_vectors") as batch:
        batch.drop_column("source_tokens")
        batch.drop_column("truncated")
