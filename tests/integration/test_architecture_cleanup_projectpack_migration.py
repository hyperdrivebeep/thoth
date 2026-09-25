from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest
from tests.integration.test_four_projectpack_portability import GenericProjectPackModel

from thoth.adapters.models import RegisteredModelResolver
from thoth.adapters.storage import AlembicSchemaMigrator, SqliteLedger
from thoth.adapters.storage import schema as storage_schema
from thoth.apps.runtime import create_runtime
from thoth.domain.migration import MigrationFailure, MigrationFailureCode
from thoth.ports.model import ModelPort
from thoth.protocol.jsonrpc import JsonRpcRequest

EXPECTED_HEAD = "2af5d3e76c81"


def _rpc(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def _alembic_versions(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    return tuple(sorted(str(row[0]) for row in rows))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ("custom-provider", "codex-oauth"))
async def test_registered_provider_runs_projectpack_without_core_change(
    tmp_path: Path,
    provider: str,
) -> None:
    root = Path(__file__).resolve().parents[2] / "examples" / "projectpacks"
    models = RegisteredModelResolver()
    custom = cast(ModelPort, GenericProjectPackModel())
    models.register(provider, lambda _model: custom)
    runtime = create_runtime(
        tmp_path / provider,
        projectpack_root=root,
        model_resolver=models,
    )
    try:
        response = await runtime.bus.dispatch(
            _rpc(
                "projectpack/run",
                "custom-provider-run",
                {
                    "project_id": "project:public-demo-membrane",
                    "pack_name": "public-demo-membrane",
                    "provider": provider,
                    "scripted": False,
                },
            )
        )
    finally:
        runtime.close()
    assert response.error is None
    assert response.result is not None
    value = response.result["value"]
    assert isinstance(value, dict)
    assert value["scripted_model"] is False


def test_runtime_bootstrap_never_calls_metadata_create_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("metadata.create_all must not be called")

    monkeypatch.setattr(storage_schema.metadata, "create_all", forbidden)
    runtime = create_runtime(tmp_path / "no-create-all")
    runtime.close()


def test_ledger_initialize_fails_closed_on_unmigrated_database(tmp_path: Path) -> None:
    ledger = SqliteLedger(tmp_path / "unmigrated.sqlite3")
    try:
        with pytest.raises(MigrationFailure, match=r"migration|required|schema"):
            ledger.initialize()
    finally:
        ledger.close()


def test_runtime_bootstrap_is_idempotent_and_stamps_single_head(tmp_path: Path) -> None:
    workspace = tmp_path / "idempotent"
    first = create_runtime(workspace)
    first.close()
    second = create_runtime(workspace)
    second.close()
    assert _alembic_versions(workspace / "db" / "thoth.sqlite3") == (EXPECTED_HEAD,)


def test_runtime_upgrades_existing_workspace_from_prior_revision(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    workspace = tmp_path / "upgrade"
    database = workspace / "db" / "thoth.sqlite3"
    database.parent.mkdir(parents=True)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", "sqlite+pysqlite:///" + database.as_posix())
    command.upgrade(config, "8c13f6b2a4d9")
    assert _alembic_versions(database) == ("8c13f6b2a4d9",)
    runtime = create_runtime(workspace)
    runtime.close()
    assert _alembic_versions(database) == (EXPECTED_HEAD,)
    with sqlite3.connect(database) as connection:
        node_columns = {row[1] for row in connection.execute("PRAGMA table_info(structural_nodes)")}
        version_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(artifact_versions)")
        }
    assert {"source_version_id", "content_json"} <= node_columns
    assert "structure_metadata_json" in version_columns


def test_runtime_rejects_database_with_multiple_version_heads(tmp_path: Path) -> None:
    workspace = tmp_path / "multi-head"
    database = workspace / "db" / "thoth.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('8c13f6b2a4d9')")
        connection.execute("INSERT INTO alembic_version VALUES ('a2b14c8d6e90')")
    with pytest.raises(MigrationFailure, match=r"multiple|head|migration") as raised:
        create_runtime(workspace)
    assert raised.value.code == MigrationFailureCode.DATABASE_HEAD_COUNT_INVALID


def test_legacy_create_all_workspace_is_safely_adopted_and_preserved(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    workspace = tmp_path / "legacy"
    database = workspace / "db" / "thoth.sqlite3"
    database.parent.mkdir(parents=True)
    # Freeze the historical pre-N02 columns rather than borrowing today's
    # metadata (which already contains columns that later migrations add).
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", "sqlite+pysqlite:///" + database.as_posix())
    command.upgrade(config, "d5e47f9013ab")
    engine = create_engine("sqlite+pysqlite:///" + database.as_posix())
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE alembic_version")
        connection.exec_driver_sql(
            "INSERT INTO projects "
            "(project_id, name, description, cutoff_at, lifecycle, overlay, policy_ref, "
            "created_at, revision) VALUES "
            "('project:legacy', 'Legacy', '', '2026-09-01T00:00:00Z', 'ACTIVE', "
            "'general-rnd', 'policy:default', '2026-09-01T00:00:00Z', 0)"
        )
    engine.dispose()

    runtime = create_runtime(workspace)
    try:
        assert runtime.bus is not None
        with runtime.ledger.engine.connect() as connection:
            name = connection.exec_driver_sql(
                "SELECT name FROM projects WHERE project_id='project:legacy'"
            ).scalar_one()
    finally:
        runtime.close()
    assert name == "Legacy"
    assert _alembic_versions(database) == (EXPECTED_HEAD,)


def test_missing_migration_repository_fails_with_typed_error(tmp_path: Path) -> None:
    with pytest.raises(MigrationFailure) as raised:
        AlembicSchemaMigrator(
            tmp_path / "missing.sqlite3",
            repository_root=tmp_path / "missing-repository",
        ).upgrade_head()
    assert raised.value.code in {
        MigrationFailureCode.SCRIPT_HEAD_COUNT_INVALID,
        MigrationFailureCode.UPGRADE_FAILED,
    }
