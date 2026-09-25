"""P7 versioned Criterion Profile payloads

Revision ID: c4d36e8f102a
Revises: b3c25d9e7f01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d36e8f102a"
down_revision: str | None = "b3c25d9e7f01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("criterion_profiles", sa.Column("profile_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("criterion_profiles", "profile_json")
