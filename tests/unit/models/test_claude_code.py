"""Claude Code binary lane uses only a fake child and synthetic profile."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from tests.unit.models.test_openai_responses_model import DemoOutput

from thoth.adapters.models.claude_code import (
    ClaudeCodeCatalog,
    ClaudeCodeModel,
    ClaudeCodeUnavailable,
    ClaudeProcess,
    claude_code_status,
)
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest
from thoth.domain.model_settings import ResolvedModelSettings


def _binary(tmp_path: Path) -> Path:
    path = tmp_path / ("claude.exe" if os.name == "nt" else "claude")
    path.write_bytes(b"synthetic-binary-never-executed")
    path.chmod(0o700)
    return path


def _connected(_workspace: Path, *, executable: Path) -> dict[str, object]:
    assert executable.is_file()
    return {"connected": True}


class FakeProcess:
    pid = 80001

    def __init__(
        self, output: dict[str, object] | None = None, *, wait_forever: bool = False
    ) -> None:
        self.returncode: int | None = None if wait_forever else 0
        self.output = output or {}
        self.wait_forever = wait_forever
        self.killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        assert input is not None and b"UNTRUSTED_EVIDENCE_SPANS" in input
        if self.wait_forever:
            await asyncio.Event().wait()
        return json.dumps(self.output).encode(), b"private fake diagnostic"

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _request_with_selection() -> ModelRequest[DemoOutput]:
    return replace(
        ModelRequest(
            role=ModelRole.HYPOTHESIS_GENERATOR,
            project_id="project:synthetic",
            cutoff_at=datetime(2026, 9, 1, tzinfo=UTC),
            context_pack=ContextPack(
                case_id="case:synthetic",
                project_id="project:synthetic",
                object_id="object:synthetic",
                problem="Synthetic boundary check",
                evidence=(),
                criteria=(),
                sufficiency=None,
                input_head_set_digest="a" * 64,
            ),
            output_model=DemoOutput,
            prompt_version="synthetic.v1",
            model_policy_ref="policy:synthetic",
            max_output_tokens=500,
        ),
        model_settings=ResolvedModelSettings(
            provider="claude-code",
            model="sonnet",
            reasoning_effort="high",
            source_by_field={},
            capability_source="synthetic",
            settings_digest="a" * 64,
        ),
    )


@pytest.mark.asyncio
async def test_cli_exact_no_tool_model_effort_schema_and_synthetic_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-never-reach-cli")
    captured: list[tuple[tuple[str, ...], dict[str, str], Path]] = []
    process = FakeProcess(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": {"label": "synthetic", "count": 2},
        }
    )

    async def spawn(argv: tuple[str, ...], env: Mapping[str, str], cwd: Path) -> FakeProcess:
        captured.append((argv, dict(env), cwd))
        return process

    model = ClaudeCodeModel(
        tmp_path / "workspace",
        model="sonnet",
        executable=_binary(tmp_path),
        process_factory=spawn,
        status_probe=_connected,
    )
    result = await model.structured(_request_with_selection())
    assert (result.output.label, result.output.count) == ("synthetic", 2)
    assert result.scripted is False
    argv, env, cwd = captured[0]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--disallowedTools") + 1] == "*"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert all(
        flag in argv
        for flag in (
            "--safe-mode",
            "--strict-mcp-config",
            "--no-chrome",
            "--no-session-persistence",
        )
    )
    assert "--bare" not in argv and "--mcp-config" not in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "high"
    raw_schema: object = json.loads(argv[argv.index("--json-schema") + 1])
    assert isinstance(raw_schema, dict)
    schema = cast(dict[str, object], raw_schema)
    assert schema["title"] == "DemoOutput"
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "high"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "500"
    assert env["MAX_STRUCTURED_OUTPUT_RETRIES"] == "1"
    assert "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_CONFIG_DIR"].startswith(str(tmp_path))
    assert cwd != tmp_path / "workspace"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        {"type": "result", "subtype": "success", "is_error": False},
        {"type": "result", "subtype": "success", "structured_output": {"count": 1}},
        {"type": "result", "subtype": "error_max_structured_output_retries"},
        {
            "type": "result",
            "subtype": "success",
            "structured_output": {"label": "x", "count": 1},
            "tool_uses": ["Bash"],
        },
    ],
)
async def test_missing_invalid_or_tool_tainted_output_fails_closed(
    tmp_path: Path, output: dict[str, object]
) -> None:
    async def spawn(_argv: tuple[str, ...], _env: Mapping[str, str], _cwd: Path) -> FakeProcess:
        return FakeProcess(output)

    model = ClaudeCodeModel(
        tmp_path / "workspace",
        model="sonnet",
        executable=_binary(tmp_path),
        process_factory=spawn,
        status_probe=_connected,
    )
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_STRUCTURED_OUTPUT_UNAVAILABLE"):
        await model.structured(_request_with_selection())


@pytest.mark.asyncio
async def test_timeout_stops_tree_and_does_not_claim_remote_stop(tmp_path: Path) -> None:
    process = FakeProcess(wait_forever=True)
    stopped: list[int] = []

    async def spawn(_argv: tuple[str, ...], _env: Mapping[str, str], _cwd: Path) -> FakeProcess:
        return process

    async def stop_tree(child: ClaudeProcess) -> bool:
        stopped.append(child.pid)
        child.kill()
        return True

    model = ClaudeCodeModel(
        tmp_path / "workspace",
        model="sonnet",
        executable=_binary(tmp_path),
        process_factory=spawn,
        stop_process_tree=stop_tree,
        timeout_seconds=0.01,
        status_probe=_connected,
    )
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_TIMEOUT_REMOTE_STOP_UNKNOWN"):
        await model.structured(_request_with_selection())
    assert stopped == [process.pid]


def test_status_and_catalog_require_supported_binary_and_login(tmp_path: Path) -> None:
    binary = _binary(tmp_path)
    captured: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        assert env["CLAUDE_CONFIG_DIR"].startswith(str(tmp_path))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "2.1.259 (Claude Code)", "")
        return subprocess.CompletedProcess(
            argv, 0, '{"loggedIn":true,"authMethod":"claude.ai subscription"}', ""
        )

    status = claude_code_status(tmp_path / "workspace", executable=binary, runner=runner)
    assert status["connection_state"] == "EXECUTION_UNVERIFIED"
    assert status["connected"] is True and status["execution_verified"] is False
    assert status["execution_eligible"] is False
    assert status["configured_auth_mode"] == "PROFILE"
    assert status["detected_auth_mode"] == "SUBSCRIPTION"
    assert status["auth_source_match"] is None
    assert len(captured) == 2 and all(argv[0] == str(binary) for argv in captured)
    catalog = ClaudeCodeCatalog(tmp_path, status=lambda _root: status)
    assert catalog.options() == ()


def test_default_catalog_and_factory_hold_before_account_or_cli_io(tmp_path: Path) -> None:
    from thoth.adapters.models.claude_code import claude_code_model_factory

    def forbidden_status(_root: Path) -> dict[str, object]:
        pytest.fail("unverified catalog must not inspect a real Claude account")

    assert ClaudeCodeCatalog(tmp_path, status=forbidden_status).options() == ()
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_EXECUTION_UNVERIFIED"):
        claude_code_model_factory(tmp_path, "sonnet")


def test_unimplemented_explicit_auth_mode_holds_without_fallback(tmp_path: Path) -> None:
    status = claude_code_status(tmp_path, auth_mode="EXPLICIT_API_KEY")
    assert status["reason_code"] == "CLAUDE_CODE_AUTH_MODE_UNSUPPORTED"
    assert status["connected"] is False
    assert ClaudeCodeCatalog(tmp_path, auth_mode="EXPLICIT_CLOUD").options() == ()
