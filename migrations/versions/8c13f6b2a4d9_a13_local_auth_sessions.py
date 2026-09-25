"""A13 local authenticated actor sessions

Revision ID: 8c13f6b2a4d9
Revises: 7b06e5a9d3f1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c13f6b2a4d9"
down_revision: str | None = "7b06e5a9d3f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("session_id", sa.String(200), primary_key=True),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("role_assignment_id", sa.String(160), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_auth_sessions_actor_id", "auth_sessions", ["actor_id"])
    op.create_index("ix_auth_sessions_project_id", "auth_sessions", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_project_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_actor_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
