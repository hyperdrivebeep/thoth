from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from thoth.cli import app


def test_bare_thoth_opens_and_exits_interactive_shell(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["--workspace", str(tmp_path / "workspace")],
        input="/help\n/quit\n",
    )

    assert result.exit_code == 0
    assert "THOTH CLI shell" in result.stdout
    assert "/rpc <JSON-RPC request>" in result.stdout


def test_profile_check_validates_without_activation() -> None:
    profile = Path(__file__).resolve().parents[1] / "config" / "profiles" / "local-codex.yaml"
    result = CliRunner().invoke(app, ["profile-check", "--profile", str(profile)])

    assert result.exit_code == 0
    assert '"status": "PASS"' in result.stdout
    assert '"activation_performed": false' in result.stdout


def test_connector_sandbox_doctor_is_non_executing() -> None:
    result = CliRunner().invoke(app, ["connector-sandbox-doctor", "--json"])

    assert result.exit_code == 0
    assert '"execution_performed": false' in result.stdout
    assert '"production_accreditation_claimed": false' in result.stdout


def test_interactive_shell_accepts_explicit_connector_registry(tmp_path: Path) -> None:
    config = Path(__file__).resolve().parents[1] / "config" / "connectors" / "local-reference.yaml"
    result = CliRunner().invoke(
        app,
        [
            "--workspace",
            str(tmp_path / "workspace"),
            "--connector-config",
            str(config),
        ],
        input="/quit\n",
    )

    assert result.exit_code == 0
    assert "THOTH CLI shell" in result.stdout
