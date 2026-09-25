"""Version-bound structural node payloads and observed parser coverage.

Existing source bytes, node rows and receipts are not rewritten.
"""

import sqlalchemy as sa
from alembic import op

revision = "2af5d3e76c81"
down_revision = "19f4c2e65b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("structural_nodes", sa.Column("source_version_id", sa.String(160), nullable=True))
    op.add_column("structural_nodes", sa.Column("content_json", sa.Text(), nullable=True))
    op.create_index(
        "ix_structural_nodes_source_version_id", "structural_nodes", ["source_version_id"]
    )
    op.add_column(
        "artifact_versions", sa.Column("structure_metadata_json", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("artifact_versions", "structure_metadata_json")
    op.drop_index("ix_structural_nodes_source_version_id", table_name="structural_nodes")
    op.drop_column("structural_nodes", "content_json")
    op.drop_column("structural_nodes", "source_version_id")
