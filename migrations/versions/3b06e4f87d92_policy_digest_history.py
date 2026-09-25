"""Allow repeated policy content across distinct immutable publications."""

import sqlalchemy as sa
from alembic import op

revision = "3b06e4f87d92"
down_revision = "2af5d3e76c81"
branch_labels = None
depends_on = None

_COLUMNS = (
    "policy_id",
    "project_id",
    "version",
    "payload_json",
    "policy_digest",
    "created_at",
)
_COLUMN_SQL = ", ".join(_COLUMNS)
_COLUMN_SHAPE = (
    ("policy_id", "VARCHAR(160)", 1, None, 1),
    ("project_id", "VARCHAR(160)", 1, None, 0),
    ("version", "INTEGER", 1, None, 0),
    ("payload_json", "TEXT", 1, None, 0),
    ("policy_digest", "VARCHAR(64)", 1, None, 0),
    ("created_at", "VARCHAR(32)", 1, None, 0),
)


def _index_columns(name: str) -> tuple[str, ...]:
    connection = op.get_bind()
    quoted = name.replace('"', '""')
    return tuple(
        str(row[2]) for row in connection.exec_driver_sql(f'PRAGMA index_info("{quoted}")')
    )


def _unique_columns() -> set[tuple[str, ...]]:
    connection = op.get_bind()
    result: set[tuple[str, ...]] = set()
    for row in connection.exec_driver_sql('PRAGMA index_list("project_policies")'):
        if not row[2]:
            continue
        result.add(_index_columns(str(row[1])))
    return result


def _check_shape(*, digest_unique: bool) -> None:
    connection = op.get_bind()
    columns = tuple(
        (str(row[1]), str(row[2]).upper(), row[3], row[4], row[5])
        for row in connection.exec_driver_sql('PRAGMA table_info("project_policies")')
    )
    expected = {("project_id", "version"), ("policy_id",)}
    if digest_unique:
        expected.add(("policy_digest",))
    indexes = tuple(connection.exec_driver_sql('PRAGMA index_list("project_policies")'))
    explicit = {
        str(row[1]): _index_columns(str(row[1]))
        for row in indexes
        if not row[2]
    }
    triggers = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='project_policies'"
    ).first()
    if (
        columns != _COLUMN_SHAPE
        or _unique_columns() != expected
        or explicit != {"ix_project_policies_project_id": ("project_id",)}
        or triggers is not None
    ):
        raise RuntimeError("project_policies schema is not the expected migration source")


def _rebuild(*, digest_unique: bool) -> None:
    temporary = "_project_policies_policy_digest_rebuild"
    op.create_table(
        temporary,
        sa.Column("policy_id", sa.String(160), primary_key=True),
        sa.Column("project_id", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False, unique=digest_unique),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("project_id", "version", name="uq_project_policy_version"),
    )
    op.execute(
        f"INSERT INTO {temporary} ({_COLUMN_SQL}) "
        f"SELECT {_COLUMN_SQL} FROM project_policies"
    )
    op.drop_index("ix_project_policies_project_id", table_name="project_policies")
    op.drop_table("project_policies")
    op.rename_table(temporary, "project_policies")
    op.create_index("ix_project_policies_project_id", "project_policies", ["project_id"])


def upgrade() -> None:
    _check_shape(digest_unique=True)
    _rebuild(digest_unique=False)


def downgrade() -> None:
    _check_shape(digest_unique=False)
    duplicate = op.get_bind().exec_driver_sql(
        "SELECT policy_digest FROM project_policies "
        "GROUP BY policy_digest HAVING count(*) > 1 LIMIT 1"
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot restore historical policy_digest uniqueness with repeated content"
        )
    _rebuild(digest_unique=True)
