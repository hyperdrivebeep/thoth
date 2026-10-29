"""Project-local derived embedding intents and immutable vector cache."""

import sqlalchemy as sa
from alembic import op

revision = "5d28a619afb4"
down_revision = "4c17f5089ea3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_configs",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "embedding_jobs",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("artifact_id", sa.String(160), nullable=False),
        sa.Column("source_version_id", sa.String(160), nullable=False),
        sa.Column("intent_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(160)),
        sa.Column("lease_until_ms", sa.Integer()),
        sa.Column("deadline_ms", sa.Integer()),
        sa.Column("reason", sa.String(100)),
        sa.Column("manifest_digest", sa.String(64)),
    )
    op.create_index("ix_embedding_jobs_project_id", "embedding_jobs", ["project_id"])
    op.create_index("ix_embedding_jobs_artifact_id", "embedding_jobs", ["artifact_id"])
    op.create_table(
        "embedding_vectors",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("content_key", sa.String(64), primary_key=True),
        sa.Column("vector_sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("embedding_vectors")
    op.drop_index("ix_embedding_jobs_artifact_id", table_name="embedding_jobs")
    op.drop_index("ix_embedding_jobs_project_id", table_name="embedding_jobs")
    op.drop_table("embedding_jobs")
    op.drop_table("embedding_configs")
