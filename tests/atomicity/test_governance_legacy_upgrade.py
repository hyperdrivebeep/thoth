import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.adapters.storage.governance_history import SqliteGovernanceHistory
from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest


async def test_populated_legacy_upgrade_retains_ids_and_marks_unknown_past(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "db/thoth.sqlite3"
    database.parent.mkdir(parents=True)
    monkeypatch.setenv("THOTH_ALEMBIC_URL", "sqlite+pysqlite:///" + database.as_posix())
    config = Config("alembic.ini")
    command.upgrade(config, "08e3b1d54a69")
    project, role, binding = "project:legacy-uow", "role:legacy-uow", "binding:legacy-uow"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO projects (project_id,name,description,cutoff_at,lifecycle,overlay,"
            "policy_ref,created_at,revision) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                project,
                "Legacy name",
                "Preserve",
                "2026-09-01T00:00:00+00:00",
                "DRAFT",
                "default",
                "policy:legacy-uow",
                "2026-08-01T00:00:00+00:00",
                7,
            ),
        )
        connection.execute(
            "INSERT INTO project_policies "
            "(policy_id,project_id,version,payload_json,policy_digest,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                "policy:legacy-uow",
                project,
                1,
                "{}",
                domain_digest(
                    "PROJECT_POLICY",
                    "1.0.0",
                    canonical_payload({"project_id": project, "policy": {}}),
                ),
                "2026-08-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO project_roles VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                role,
                project,
                "human:legacy",
                None,
                "reviewer",
                "PROJECT",
                '["CAP_READ"]',
                "ACTIVE",
                "2026-08-01T00:00:00+00:00",
                None,
            ),
        )
        connection.execute(
            "INSERT INTO source_bindings VALUES (?,?,?,?,?,?,?)",
            (
                binding,
                project,
                "artifact:legacy",
                "READ",
                "ACTIVE",
                "2026-08-01T00:00:00+00:00",
                "2026-08-02T00:00:00+00:00",
            ),
        )
        originals = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("projects", "project_policies", "project_roles", "source_bindings")
        }
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert all(
            connection.execute(f"SELECT * FROM {table}").fetchall() == rows
            for table, rows in originals.items()
        )
        assert connection.execute("SELECT count(*) FROM governance_revisions").fetchone()[0] == 3
    command.upgrade(config, "head")
    runtime = create_runtime(tmp_path)
    try:
        history = SqliteGovernanceHistory(runtime.ledger.engine)
        baseline = history.read_current(project, "PROJECT", project)
        assert baseline is not None and baseline.origin == "LEGACY_BASELINE"
        assert baseline.previous_digest is None and baseline.actor_ref == "UNKNOWN_LEGACY"
        assert baseline.projection["revision"] == 7
        current = value(
            await runtime.bus.query(request("project/read", "legacy-read", {"project_id": project}))
        )
        assert current["name"] == "Legacy name" and current["revision"] == 7
        changed = value(
            await runtime.bus.dispatch(
                request(
                    "project/metadata/update",
                    "legacy-edit",
                    {"project_id": project, "expected_revision": 7, "name": "New verified name"},
                )
            )
        )
        assert changed["revision"] == 8
        latest = history.read_current(project, "PROJECT", project)
        assert latest is not None and latest.origin == "RECORDED"
        assert latest.previous_digest == baseline.record_digest
        assert len(history.history(project, "PROJECT", project)) == 2
        governance = SqliteGovernanceStore(runtime.ledger.engine)
        assert governance.list_roles(project)[0].role_assignment_id == role
        assert governance.list_roles(project)[0].authority_tags == ("CAP_READ",)
        assert governance.list_source_bindings(project)[0].binding_id == binding
        assert governance.list_source_bindings(project)[0].state == "ACTIVE"
        role_baseline = history.read_current(project, "ROLE", role)
        binding_baseline = history.read_current(project, "SOURCE_BINDING", binding)
        assert role_baseline is not None and role_baseline.origin == "LEGACY_BASELINE"
        assert binding_baseline is not None and binding_baseline.origin == "LEGACY_BASELINE"
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        records = SqliteGovernanceHistory(reopened.ledger.engine).history(
            project, "PROJECT", project
        )
        assert [r.projection["name"] for r in records] == [
            "Legacy name",
            "New verified name",
        ]
    finally:
        reopened.close()
