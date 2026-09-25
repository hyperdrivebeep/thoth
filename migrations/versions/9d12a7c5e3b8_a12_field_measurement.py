"""A12 privacy-safe field measurement ledger

Revision ID: 9d12a7c5e3b8
Revises: 8c13f6b2a4d9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d12a7c5e3b8"
down_revision: str | None = "8c13f6b2a4d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = (
        (
            "field_protocol_seals",
            (
                sa.Column("protocol_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("protocol_digest", sa.String(64), nullable=False, unique=True),
                sa.Column("content_json", sa.Text(), nullable=False),
            ),
        ),
        (
            "field_sessions",
            (
                sa.Column("session_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("protocol_digest", sa.String(64), nullable=False),
                sa.Column("state", sa.String(40), nullable=False),
                sa.Column("content_json", sa.Text(), nullable=False),
            ),
        ),
        (
            "field_events",
            (
                sa.Column("event_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("session_id", sa.String(200), nullable=False),
                sa.Column("event_type", sa.String(80), nullable=False),
                sa.Column("content_json", sa.Text(), nullable=False),
            ),
        ),
        (
            "field_session_metrics",
            (
                sa.Column("session_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("content_json", sa.Text(), nullable=False),
            ),
        ),
        (
            "field_scores",
            (
                sa.Column("score_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("session_id", sa.String(200), nullable=False),
                sa.Column("content_json", sa.Text(), nullable=False),
            ),
        ),
        (
            "field_exports",
            (
                sa.Column("export_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("protocol_digest", sa.String(64), nullable=False),
                sa.Column("bundle_digest", sa.String(64), nullable=False, unique=True),
                sa.Column("content_json", sa.Text(), nullable=False),
            ),
        ),
    )
    for name, columns in tables:
        op.create_table(name, *columns)
        op.create_index(f"ix_{name}_project_id", name, ["project_id"])
    op.create_index("ix_field_events_session_id", "field_events", ["session_id"])
    op.create_index("ix_field_scores_session_id", "field_scores", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_field_scores_session_id", table_name="field_scores")
    op.drop_index("ix_field_events_session_id", table_name="field_events")
    for name in (
        "field_exports",
        "field_scores",
        "field_session_metrics",
        "field_events",
        "field_sessions",
        "field_protocol_seals",
    ):
        op.drop_index(f"ix_{name}_project_id", table_name=name)
        op.drop_table(name)
