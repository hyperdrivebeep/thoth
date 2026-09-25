"""F1 project governance and optimistic project revision

Revision ID: a31f6b8c4d20
Revises: f9a43b25d201
Create Date: 2026-08-31 00:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a31f6b8c4d20"
down_revision: str | None = "f9a43b25d201"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "organizations",
        sa.Column("organization_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("organization_id"),
    )
    op.create_table(
        "project_roles",
        sa.Column("role_assignment_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("actor_id", sa.String(length=160), nullable=False),
        sa.Column("organization_id", sa.String(length=160), nullable=True),
        sa.Column("role", sa.String(length=160), nullable=False),
        sa.Column("scope", sa.String(length=260), nullable=False),
        sa.Column("authority_tags_json", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("revoked_at", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("role_assignment_id"),
    )
    op.create_index("ix_project_roles_project_id", "project_roles", ["project_id"])
    op.create_index("ix_project_roles_actor_id", "project_roles", ["actor_id"])
    op.create_table(
        "workstreams",
        sa.Column("workstream_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("parent_workstream_id", sa.String(length=160), nullable=True),
        sa.Column("owner_role_assignment_id", sa.String(length=160), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("workstream_id"),
    )
    op.create_index("ix_workstreams_project_id", "workstreams", ["project_id"])
    op.create_table(
        "project_policies",
        sa.Column("policy_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("policy_id"),
        sa.UniqueConstraint("policy_digest"),
        sa.UniqueConstraint("project_id", "version", name="uq_project_policy_version"),
    )
    op.create_index("ix_project_policies_project_id", "project_policies", ["project_id"])
    op.create_table(
        "project_references",
        sa.Column("reference_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("origin_project_id", sa.String(length=160), nullable=False),
        sa.Column("origin_revision", sa.String(length=160), nullable=False),
        sa.Column("rights_status", sa.String(length=80), nullable=False),
        sa.Column("scope", sa.String(length=260), nullable=False),
        sa.Column("authority_status", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("reference_id"),
    )
    op.create_index("ix_project_references_project_id", "project_references", ["project_id"])
    op.create_table(
        "source_bindings",
        sa.Column("binding_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.String(length=160), nullable=False),
        sa.Column("artifact_id", sa.String(length=160), nullable=False),
        sa.Column("capability", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("binding_id"),
        sa.UniqueConstraint("project_id", "artifact_id", name="uq_source_binding_artifact"),
    )
    op.create_index("ix_source_bindings_project_id", "source_bindings", ["project_id"])
    op.create_index("ix_source_bindings_artifact_id", "source_bindings", ["artifact_id"])


def downgrade() -> None:
    op.drop_index("ix_source_bindings_artifact_id", table_name="source_bindings")
    op.drop_index("ix_source_bindings_project_id", table_name="source_bindings")
    op.drop_table("source_bindings")
    op.drop_index("ix_project_references_project_id", table_name="project_references")
    op.drop_table("project_references")
    op.drop_index("ix_project_policies_project_id", table_name="project_policies")
    op.drop_table("project_policies")
    op.drop_index("ix_workstreams_project_id", table_name="workstreams")
    op.drop_table("workstreams")
    op.drop_index("ix_project_roles_actor_id", table_name="project_roles")
    op.drop_index("ix_project_roles_project_id", table_name="project_roles")
    op.drop_table("project_roles")
    op.drop_table("organizations")
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("revision")
        batch_op.drop_column("description")
