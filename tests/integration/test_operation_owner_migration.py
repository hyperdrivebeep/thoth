"""Old RUNNING operations stay ownerless; new DDL is one additive table."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, insert
from tests.integration.scoped_runtime import create_runtime
from tests.integration.test_operation_owner_storage import candidate, owner

from thoth.adapters.storage.operations import SqliteOperationStore
from thoth.adapters.storage.schema import idempotency_keys, operations
from thoth.domain.enums import OperationState
from thoth.protocol.registry import PUBLIC_METHODS


def test_upgrade_keeps_old_rows_and_reopen_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "old.sqlite3"
    url = "sqlite+pysqlite:///" + database.as_posix()
    monkeypatch.setenv("THOTH_ALEMBIC_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "3b06e4f87d92")
    original = candidate()
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            insert(operations).values(
                operation_id=original.operation_id,
                project_id=original.project_id,
                method=original.method,
                idempotency_key=original.idempotency_key,
                scope_digest=original.scope_digest,
                state="RUNNING",
                created_at=original.created_at.isoformat(),
                epoch=0,
                owner_data_scopes_json="[]",
            )
        )
        connection.execute(
            insert(idempotency_keys).values(
                project_id=original.project_id,
                method=original.method,
                idempotency_key=original.idempotency_key,
                scope_digest=original.scope_digest,
                operation_id=original.operation_id,
            )
        )
    engine.dispose()
    with closing(sqlite3.connect(database)) as connection:
        before = connection.execute("SELECT * FROM operations").fetchall()
        columns = connection.execute("PRAGMA table_info(operations)").fetchall()
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        store = SqliteOperationStore(engine)
        stored = store.read(original.operation_id)
        assert stored is not None and stored == original and stored.owner is None
        assert store.claim(original.model_copy(update={"owner": owner()})) == (original, True)
        replayed = store.read(original.operation_id)
        assert replayed is not None and replayed.state is OperationState.RUNNING
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT count(*) FROM operation_owners").scalar_one()
                == 0
            )
    finally:
        engine.dispose()
    command.downgrade(config, "3b06e4f87d92")
    command.upgrade(config, "head")
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT * FROM operations").fetchall() == before
        assert connection.execute("PRAGMA table_info(operations)").fetchall() == columns
        assert connection.execute("SELECT count(*) FROM operation_owners").fetchone() == (0,)


def test_sealable_fixture_is_the_approved_67_registered_methods(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/sealable_methods.json"
    record = json.loads(fixture.read_text(encoding="utf-8"))
    methods = record["methods"]
    assert methods == sorted(set(methods)) and len(methods) == 67
    assert set(methods) <= set(PUBLIC_METHODS)
    runtime = create_runtime(tmp_path / "workspace")
    try:
        for method in methods:
            assert callable(runtime.bus._registry.resolve(method))  # pyright: ignore[reportPrivateUsage]
    finally:
        runtime.close()
    assert not set(methods).intersection(
        {
            "thread/start",
            "thread/input",
            "thread/steer",
            "thread/pause",
            "thread/resume",
            "thread/stop",
            "revision/restore",
            "revision/restore/apply",
            "revision/changeSet/commit",
            "project/archive",
            "project/delete",
            "project/restore",
            "workspace/setup/update",
            "improvement/evaluation/run",
            "action/preflight/read",
        }
    )
    digest = hashlib.sha256(("\n".join(methods) + "\n").encode()).hexdigest()
    assert digest == "83993c93cbe89314247caa791d292cbc806a73d0c955d424d8fcf48ebeb18da4"
    assert record["evidence"]["approved_methods_sha256"] == digest
    assert record["evidence"]["path"] == "outputs/refactor-vs2-20261019/sealable-evidence.json"
