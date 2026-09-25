"""Preserve observed mutable governance state without inventing older revisions."""

import hashlib
import json
import unicodedata
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "19f4c2e65b70"
down_revision = "08e3b1d54a69"
branch_labels = None
depends_on = None


def _normalize(value):
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {_normalize(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _json(value):
    return json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(domain, value):
    return hashlib.sha256((f"THOTH/{domain}/1.0.0\0" + _json(value)).encode()).hexdigest()


def upgrade() -> None:
    op.create_table(
        "governance_revisions",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("record_kind", sa.String(32), primary_key=True),
        sa.Column("record_id", sa.String(200), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("record_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("receipt_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("receipt_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "governance_heads",
        sa.Column("project_id", sa.String(160), primary_key=True),
        sa.Column("record_kind", sa.String(32), primary_key=True),
        sa.Column("record_id", sa.String(200), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("record_digest", sa.String(64), nullable=False),
    )
    connection = op.get_bind()
    observed_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    for table, kind, identity in (
        ("projects", "PROJECT", "project_id"),
        ("project_roles", "ROLE", "role_assignment_id"),
        ("source_bindings", "SOURCE_BINDING", "binding_id"),
    ):
        for row in connection.execute(sa.text(f"SELECT * FROM {table}")).mappings().all():
            body = {
                "project_id": row["project_id"],
                "record_kind": kind,
                "record_id": row[identity],
                "revision": 1,
                "previous_digest": None,
                "origin": "LEGACY_BASELINE",
                "projection": dict(row),
                "related_refs": {},
                "actor_ref": "UNKNOWN_LEGACY",
                "recorded_at": observed_at,
                "schema_version": "1.0.0",
            }
            digest = _digest("PROJECT_GOVERNANCE_REVISION", body)
            receipt = {
                "project_id": row["project_id"],
                "record_kind": kind,
                "record_id": row[identity],
                "before_digest": None,
                "after_digest": digest,
                "origin": "LEGACY_BASELINE",
                "actor_ref": "UNKNOWN_LEGACY",
                "recorded_at": observed_at,
                "schema_version": "1.0.0",
            }
            receipt_digest = _digest("PROJECT_GOVERNANCE_RECEIPT", receipt)
            params = {
                "project": row["project_id"],
                "kind": kind,
                "record": row[identity],
                "digest": digest,
                "content": _json({**body, "record_digest": digest}),
                "receipt_digest": receipt_digest,
                "receipt": _json({**receipt, "receipt_digest": receipt_digest}),
            }
            connection.execute(
                sa.text(
                    "INSERT INTO governance_revisions VALUES "
                    "(:project,:kind,:record,1,:digest,:content,:receipt_digest,:receipt)"
                ),
                params,
            )
            connection.execute(
                sa.text("INSERT INTO governance_heads VALUES (:project,:kind,:record,1,:digest)"),
                params,
            )


def downgrade() -> None:
    op.drop_table("governance_heads")
    op.drop_table("governance_revisions")
