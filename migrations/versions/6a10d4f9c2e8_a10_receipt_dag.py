"""A10 typed receipt DAG foundation

Revision ID: 6a10d4f9c2e8
Revises: 5a02d4c8f1b7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a10d4f9c2e8"
down_revision: str | None = "5a02d4c8f1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name, columns in (
        (
            "receipt_dag_nodes",
            (
                sa.Column("node_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("kind", sa.String(40), nullable=False),
                sa.Column("content_json", sa.Text(), nullable=False),
                sa.Column("node_digest", sa.String(64), nullable=False, unique=True),
            ),
        ),
        (
            "receipt_dag_edges",
            (
                sa.Column("edge_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("parent_node_id", sa.String(200), nullable=False),
                sa.Column("child_node_id", sa.String(200), nullable=False),
                sa.Column("content_json", sa.Text(), nullable=False),
                sa.Column("edge_digest", sa.String(64), nullable=False, unique=True),
            ),
        ),
        (
            "receipt_dag_manifests",
            (
                sa.Column("manifest_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False, unique=True),
                sa.Column("content_json", sa.Text(), nullable=False),
                sa.Column("manifest_digest", sa.String(64), nullable=False),
            ),
        ),
        (
            "receipt_dag_bundles",
            (
                sa.Column("bundle_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("content_json", sa.Text(), nullable=False),
                sa.Column("bundle_digest", sa.String(64), nullable=False, unique=True),
            ),
        ),
        (
            "receipt_dag_verifications",
            (
                sa.Column("verification_id", sa.String(200), primary_key=True),
                sa.Column("project_id", sa.String(160), nullable=False),
                sa.Column("subject_id", sa.String(200), nullable=False),
                sa.Column("content_json", sa.Text(), nullable=False),
                sa.Column("verification_digest", sa.String(64), nullable=False, unique=True),
            ),
        ),
    ):
        op.create_table(name, *columns)
        op.create_index(f"ix_{name}_project_id", name, ["project_id"])


def downgrade() -> None:
    for name in (
        "receipt_dag_verifications",
        "receipt_dag_bundles",
        "receipt_dag_manifests",
        "receipt_dag_edges",
        "receipt_dag_nodes",
    ):
        op.drop_index(f"ix_{name}_project_id", table_name=name)
        op.drop_table(name)
