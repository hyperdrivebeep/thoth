"""Add the immutable test-validity projection; existing evidence snapshots are unchanged."""

import sqlalchemy as sa
from alembic import op

revision = "c49f63b10d25"
down_revision = "b38e52a09c14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_test_assessments",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("assessment_id", sa.String(200), primary_key=True),
        sa.Column("prediction_id", sa.String(200), nullable=False),
        sa.Column("attempt_ref", sa.String(200), nullable=False),
        sa.Column("assessment_digest", sa.String(64), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "uq_research_test_prediction_attempt",
        "research_test_assessments",
        ["project_id", "prediction_id", "attempt_ref"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_research_test_prediction_attempt", table_name="research_test_assessments")
    op.drop_table("research_test_assessments")
