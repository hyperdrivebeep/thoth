"""CLI registration, text, failures and source staging remain the same after extraction."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest
from typer.testing import CliRunner

import thoth.cli as cli

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/cli_registration_contract.json"


def capture_environment() -> dict[str, str]:
    return {
        **os.environ,
        "COLUMNS": "120",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def test_registration_help_and_parse_failures_match_original() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tests.cli_contract_capture"],
        cwd=ROOT,
        env=capture_environment(),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert (
        tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"][
            "thoth"
        ]
        == "thoth.cli:app"
    )


def test_module_entry_renders_the_same_root_help_without_opening_a_workspace() -> None:
    code = (
        "from pathlib import Path; from unittest.mock import patch; import runpy,sys; "
        "from thoth.apps import workspace_paths; "
        "patcher=patch.object(workspace_paths,'default_workspace',"
        "return_value=Path('contract-workspace')); "
        "patcher.start(); sys.argv=['thoth','--help']; "
        "runpy.run_module('thoth.cli',run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=capture_environment(),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["outputs"]["--help"]
    assert result.returncode == expected["exit_code"] == 0
    assert result.stdout == expected["stdout"]
    assert result.stderr == expected["stderr"]


def test_doctor_reports_missing_capability_with_failure_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = importlib.util.find_spec

    def find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        return None if name == "fastapi" else original(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    result = CliRunner().invoke(cli.app, ["doctor", "--workspace", str(tmp_path / "ws"), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "FAIL" and payload["checks"]["fastapi"] is False
    assert result.stderr == ""


def test_source_stage_bytes_output_idempotency_and_conflict(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_bytes(b"synthetic evidence\n")
    workspace = tmp_path / "workspace"
    args = ["source-stage", "--source", str(source), "--workspace", str(workspace)]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    first = CliRunner().invoke(cli.app, args)
    assert first.exit_code == 0 and first.stderr == ""
    assert json.loads(first.stdout) == {
        "relative_path": f"{digest[:16]}-input.txt",
        "byte_sha256": digest,
        "bytes": 19,
    }
    staged = workspace / "inbox" / f"{digest[:16]}-input.txt"
    assert staged.read_bytes() == source.read_bytes()
    repeated = CliRunner().invoke(cli.app, args)
    assert (repeated.exit_code, repeated.stdout, repeated.stderr) == (
        first.exit_code,
        first.stdout,
        first.stderr,
    )
    staged.write_bytes(b"different")
    conflict = CliRunner().invoke(cli.app, args)
    assert conflict.exit_code == 2
    assert "existing staged path has different content" in conflict.stderr
    assert staged.read_bytes() == b"different"


def test_git_snapshot_keeps_manifest_bytes_and_git_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "sample"
    repository.mkdir()
    head = "a" * 40
    object_id = "b" * 40
    responses: dict[tuple[str, ...], bytes] = {
        ("rev-parse", "--show-toplevel"): (str(repository.resolve()) + "\n").encode(),
        ("rev-parse", "HEAD"): (head + "\n").encode(),
        ("status", "--porcelain=v1", "-z"): b"A  file.txt\0",
        ("ls-files", "--stage", "-z"): f"100644 {object_id} 0\tfile.txt\0".encode(),
    }
    calls: list[tuple[str, ...]] = []

    def git_read(
        args: list[str], *, capture_output: bool, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert args[:3] == ["git", "-C", str(repository.resolve())]
        assert capture_output is True and check is False
        key = tuple(args[3:])
        calls.append(key)
        return subprocess.CompletedProcess(args, 0, stdout=responses[key], stderr=b"")

    monkeypatch.setattr(subprocess, "run", git_read)
    workspace = tmp_path / "workspace"
    result = CliRunner().invoke(
        cli.app,
        [
            "git-snapshot",
            "--repository",
            str(repository),
            "--workspace",
            str(workspace),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    data = (workspace / "inbox" / payload["relative_path"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == payload["byte_sha256"]
    assert json.loads(data) == {
        "repository": "sample",
        "head": head,
        "dirty": True,
        "entries": [
            {
                "path": "file.txt",
                "mode": "100644",
                "object_id": object_id,
                "stage": 0,
                "status": "A ",
            }
        ],
    }
    assert payload["entry_count"] == 1 and payload["head"] == head
    assert calls == list(responses)
