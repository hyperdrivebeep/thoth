"""CLI Codex entries bind one explicit workspace and never invoke the old CLI agent."""

from __future__ import annotations

from pathlib import Path

import pytest

import thoth.cli as cli
from thoth.domain.model import ModelRequest, ModelResult


class FakeModel:
    def __init__(self, *, scripted: bool = False) -> None:
        self.requests: list[ModelRequest[cli.ModelProbeOutput]] = []
        self.scripted = scripted

    async def structured(
        self, request: ModelRequest[cli.ModelProbeOutput]
    ) -> ModelResult[cli.ModelProbeOutput]:
        self.requests.append(request)
        return ModelResult(
            output=cli.ModelProbeOutput(status="OK", message="synthetic"),
            model_id="codex-oauth/synthetic",
            prompt_version=request.prompt_version,
            scripted=self.scripted,
            input_digest="a" * 64,
            output_digest="b" * 64,
        )


def _fake_pack(_root: Path, *, include_scripted: bool = False) -> object:
    del include_scripted
    return object()


def test_auth_status_and_connect_use_selected_workspace_and_sanitize_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "isolated"
    reads: list[Path] = []
    starts: list[tuple[Path, bool]] = []
    closed: list[Path] = []

    def status(root: Path) -> dict[str, object]:
        reads.append(root)
        return {
            "connected": False,
            "connection_state": "LOGIN_REQUIRED",
            "token": "never-print-status-token",
        }

    def login(root: Path, *, wait_for_completion: bool = False) -> dict[str, object]:
        starts.append((root, wait_for_completion))
        return {
            "started": True,
            "connected": True,
            "connection_state": "EXECUTION_UNVERIFIED",
            "execution_eligible": True,
            "execution_verified": False,
            "reason_code": None,
            "guidance": "Synthetic completion",
            "auth_url": "never-print-auth-url",
            "token": "never-print-token",
        }

    monkeypatch.setattr(cli, "codex_oauth_status", status)
    monkeypatch.setattr(cli, "start_codex_login", login)
    monkeypatch.setattr(cli, "close_workspace_broker", closed.append)
    with pytest.raises(cli.typer.Exit):
        cli.auth_status(workspace=workspace, json_output=True)
    cli.auth_connect(workspace=workspace)
    output = capsys.readouterr().out
    assert reads == [workspace, workspace]
    assert starts == [(workspace, True)]
    assert closed == [workspace, workspace]
    assert "never-print-auth-url" not in output and "never-print-token" not in output
    assert "never-print-status-token" not in output
    assert "EXECUTION_UNVERIFIED" in output


def test_model_probe_uses_same_factory_model_port_and_structured_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "selected"
    fake = FakeModel()
    calls: list[tuple[Path, str | None]] = []
    closed: list[Path] = []

    def factory(root: Path, model: str | None = None) -> FakeModel:
        calls.append((root, model))
        return fake

    monkeypatch.setattr(cli, "create_codex_model", factory)
    monkeypatch.setattr(cli, "close_workspace_broker", closed.append)
    cli.model_probe(model="gpt-synthetic", workspace=workspace)
    assert calls == [(workspace, "gpt-synthetic")]
    assert closed == [workspace]
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.project_id == request.context_pack.project_id == "system:workspace"
    assert request.output_model is cli.ModelProbeOutput
    assert "synthetic" in capsys.readouterr().out


def test_auth_connect_timeout_closes_broker_without_exposing_login_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "isolated"
    closed: list[Path] = []

    def status(_root: Path) -> dict[str, object]:
        return {"connected": False, "connection_state": "LOGIN_REQUIRED"}

    def login(_root: Path, *, wait_for_completion: bool = False) -> dict[str, object]:
        assert wait_for_completion is True
        return {
            "started": True,
            "connected": False,
            "connection_state": "LOGIN_REQUIRED",
            "reason_code": "CODEX_LOGIN_TIMEOUT",
            "auth_url": "never-print-auth-url",
        }

    monkeypatch.setattr(cli, "codex_oauth_status", status)
    monkeypatch.setattr(cli, "start_codex_login", login)
    monkeypatch.setattr(cli, "close_workspace_broker", closed.append)
    with pytest.raises(cli.typer.Exit):
        cli.auth_connect(workspace=workspace)
    assert closed == [workspace]
    output = capsys.readouterr().out
    assert "CODEX_LOGIN_TIMEOUT" in output
    assert "never-print-auth-url" not in output


def test_model_probe_does_not_report_scripted_output_as_live_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeModel(scripted=True)

    def factory(_root: Path, _model: str | None = None) -> FakeModel:
        return fake

    def close(_root: Path) -> None:
        return None

    monkeypatch.setattr(cli, "create_codex_model", factory)
    monkeypatch.setattr(cli, "close_workspace_broker", close)
    with pytest.raises(cli.typer.Exit):
        cli.model_probe(model="gpt-synthetic", workspace=tmp_path)


def test_codex_run_pack_requires_explicit_matching_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(cli.typer.BadParameter, match="explicit --workspace"):
        cli.run_pack(pack=tmp_path, workspace=None, provider="codex-oauth")

    workspace = tmp_path / "isolated"
    fake = FakeModel()
    calls: list[tuple[Path, str | None]] = []
    runs: list[tuple[Path, object | None]] = []
    closed: list[Path] = []

    def factory(root: Path, model: str | None = None) -> FakeModel:
        calls.append((root, model))
        return fake

    class Result:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"status": "synthetic"}

    async def run(_pack: object, *, workspace: Path, model: object | None = None) -> Result:
        runs.append((workspace, model))
        return Result()

    monkeypatch.setattr(cli, "load_project_pack", _fake_pack)
    monkeypatch.setattr(cli, "create_codex_model", factory)
    monkeypatch.setattr(cli, "run_project_pack", run)
    monkeypatch.setattr(cli, "close_workspace_broker", closed.append)
    cli.run_pack(
        pack=tmp_path, workspace=workspace, provider="codex-oauth", model_name="gpt-synthetic"
    )
    assert calls == [(workspace.resolve(), "gpt-synthetic")]
    assert runs == [(workspace.resolve(), fake)]
    assert closed == [workspace.resolve()]


def test_scripted_run_pack_preserves_relative_default_without_codex_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[Path, object | None]] = []

    class Result:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            return {"status": mode}

    async def run(_pack: object, *, workspace: Path, model: object | None = None) -> Result:
        observed.append((workspace, model))
        return Result()

    def reject_codex(_root: Path, _model: str | None = None) -> FakeModel:
        pytest.fail("Codex selected")

    monkeypatch.setattr(cli, "load_project_pack", _fake_pack)
    monkeypatch.setattr(cli, "run_project_pack", run)
    monkeypatch.setattr(cli, "create_codex_model", reject_codex)
    cli.run_pack(pack=tmp_path, workspace=None, provider="scripted")
    assert observed == [(Path(".thoth-pack"), None)]
