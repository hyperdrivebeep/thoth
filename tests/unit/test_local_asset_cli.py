import os
import subprocess
from pathlib import Path

import pytest
import typer
from tests.embedding_fixtures import sample_assets
from typer.testing import CliRunner

from thoth.adapters.embeddings import assets
from thoth.adapters.embeddings.onnx_encoder import GraniteOnnxEncoder
from thoth.adapters.storage.bundle import SqliteStoreFactory
from thoth.apps.local_asset_commands import register_local_asset_commands


def test_offline_cli_import_status_without_encoder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def forbidden_storage(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("CLI_OPENED_STORAGE")

    def forbidden_encoder(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("CLI_STARTED_ENCODER")

    monkeypatch.setattr(SqliteStoreFactory, "open", forbidden_storage)
    monkeypatch.setattr(GraniteOnnxEncoder, "__init__", forbidden_encoder)
    monkeypatch.setattr(subprocess, "Popen", forbidden_encoder)
    if os.name == "nt":
        from thoth.adapters.models import windows_process_job

        monkeypatch.setattr(windows_process_job, "spawn_windows_job", forbidden_encoder)
    source, store = sample_assets(tmp_path)
    monkeypatch.setattr(assets, "granite_manifest", lambda: store.manifest)
    workspace = tmp_path / "workspace"
    app = typer.Typer()
    register_local_asset_commands(app, workspace)
    runner = CliRunner()
    missing = runner.invoke(app, ["local-model", "status"])
    assert missing.exit_code == 0 and '"state":"MODEL_REQUIRED"' in missing.stdout
    assert not workspace.exists()
    imported = runner.invoke(app, ["local-model", "import", str(source)])
    assert imported.exit_code == 0, imported.output
    assert '"state":"READY"' in imported.stdout
    checked = runner.invoke(app, ["local-model", "status"])
    assert checked.exit_code == 0 and '"state":"READY"' in checked.stdout
    assert not (workspace / "db").exists(), "CLI_CREATED_DATABASE_DIRECTORY"


def test_hosted_cli_refuses_before_local_directory_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "HOSTED_REVIEW")
    workspace = tmp_path / "unopened"
    app = typer.Typer()
    register_local_asset_commands(app, workspace)
    result = CliRunner().invoke(app, ["local-model", "status"])
    assert result.exit_code != 0 and "LOCAL_RETRIEVAL_LOCAL_ONLY" in result.output
    assert not workspace.exists()
