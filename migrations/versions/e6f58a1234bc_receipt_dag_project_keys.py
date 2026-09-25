"""Project-scoped Receipt DAG node and edge keys.

Revision ID: e6f58a1234bc
Revises: d5e47f9013ab
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "e6f58a1234bc"
down_revision: str | None = "d5e47f9013ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(identifier: str, *, edge: bool) -> tuple[sa.Column[Any], ...]:
    values: list[sa.Column[Any]] = [
        sa.Column(identifier, sa.String(200), primary_key=True),
        sa.Column("project_id", sa.String(160), primary_key=True),
    ]
    if edge:
        values.extend(
            (
                sa.Column("parent_node_id", sa.String(200), nullable=False),
                sa.Column("child_node_id", sa.String(200), nullable=False),
            )
        )
    else:
        values.append(sa.Column("kind", sa.String(40), nullable=False))
    values.extend(
        (
            sa.Column("content_json", sa.Text(), nullable=False),
            sa.Column(
                "edge_digest" if edge else "node_digest",
                sa.String(64),
                nullable=False,
                unique=True,
            ),
        )
    )
    return tuple(values)


def _rebuild(name: str, identifier: str, *, edge: bool, composite: bool) -> None:
    temporary = f"_{name}_project_key"
    columns = list(_columns(identifier, edge=edge))
    if not composite:
        columns[1] = sa.Column("project_id", sa.String(160), nullable=False)
        columns[0] = sa.Column(identifier, sa.String(200), primary_key=True)
    op.create_table(temporary, *columns)
    selected = (
        f"{identifier}, project_id, parent_node_id, child_node_id, content_json, edge_digest"
        if edge
        else f"{identifier}, project_id, kind, content_json, node_digest"
    )
    op.execute(sa.text(f"INSERT INTO {temporary} ({selected}) SELECT {selected} FROM {name}"))
    op.drop_index(f"ix_{name}_project_id", table_name=name)
    op.drop_table(name)
    op.rename_table(temporary, name)
    op.create_index(f"ix_{name}_project_id", name, ["project_id"])


def upgrade() -> None:
    _rebuild("receipt_dag_nodes", "node_id", edge=False, composite=True)
    _rebuild("receipt_dag_edges", "edge_id", edge=True, composite=True)


def _assert_downgrade_safe(name: str, identifier: str) -> None:
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT {identifier} FROM {name} "
                f"GROUP BY {identifier} HAVING COUNT(DISTINCT project_id) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            f"cannot downgrade {name}: cross-project duplicate identifiers would be lost"
        )


def downgrade() -> None:
    _assert_downgrade_safe("receipt_dag_nodes", "node_id")
    _assert_downgrade_safe("receipt_dag_edges", "edge_id")
    _rebuild("receipt_dag_edges", "edge_id", edge=True, composite=False)
    _rebuild("receipt_dag_nodes", "node_id", edge=False, composite=False)
