"""A02 acquisition search intents and evidence leads

Revision ID: 5a02d4c8f1b7
Revises: 4edb1326e7ba
Create Date: 2026-09-01 08:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5a02d4c8f1b7"
down_revision: str | None = "4edb1326e7ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "acquisition_search_intents",
        sa.Column("search_intent_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("investigation_id", sa.String(160), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("intent_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index(
        "ix_acquisition_search_intents_project_id",
        "acquisition_search_intents",
        ["project_id"],
    )
    op.create_index(
        "ix_acquisition_search_intents_investigation_id",
        "acquisition_search_intents",
        ["investigation_id"],
    )
    op.create_table(
        "evidence_leads",
        sa.Column("lead_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("investigation_id", sa.String(160), nullable=False),
        sa.Column("search_intent_id", sa.String(160), nullable=False),
        sa.Column("source_id", sa.String(160), nullable=False),
        sa.Column("span_id", sa.String(160), nullable=False),
        sa.Column("state", sa.String(60), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("lead_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    for column in (
        "project_id",
        "investigation_id",
        "search_intent_id",
        "source_id",
        "span_id",
        "state",
    ):
        op.create_index(f"ix_evidence_leads_{column}", "evidence_leads", [column])


def downgrade() -> None:
    for column in (
        "state",
        "span_id",
        "source_id",
        "search_intent_id",
        "investigation_id",
        "project_id",
    ):
        op.drop_index(f"ix_evidence_leads_{column}", table_name="evidence_leads")
    op.drop_table("evidence_leads")
    op.drop_index(
        "ix_acquisition_search_intents_investigation_id",
        table_name="acquisition_search_intents",
    )
    op.drop_index(
        "ix_acquisition_search_intents_project_id",
        table_name="acquisition_search_intents",
    )
    op.drop_table("acquisition_search_intents")
