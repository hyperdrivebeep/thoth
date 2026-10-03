"""Claude Code binary lane uses only a fake child and synthetic profile."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from tests.unit.models.test_openai_responses_model import DemoOutput

from thoth.adapters.models.claude_code import (
    ClaudeCodeCatalog,
    ClaudeCodeModel,
    ClaudeCodeStatusCache,
    ClaudeCodeUnavailable,
    ClaudeProcess,
    claude_code_status,
)
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelControlCapability
from thoth.domain.model_settings import ResolvedModelSettings
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef
from thoth.ports.model import ModelExecutionHold


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
        self,
        output: dict[str, object] | None = None,
        *,
        wait_forever: bool = False,
        exit_code: int = 0,
    ) -> None:
        self.returncode: int | None = None if wait_forever else exit_code
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
    assert schema["additionalProperties"] is False
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "high"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "500"
    assert env["MAX_STRUCTURED_OUTPUT_RETRIES"] == "1"
    assert "ANTHROPIC_API_KEY" not in env
    assert env["DISABLE_AUTOUPDATER"] == "1"
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


# What claude 2.1.284 printed for a profile that is not signed in (exit code 1).
NOT_LOGGED_IN = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "result": "Not logged in · Please run /login",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [1, 0])
async def test_a_profile_that_is_not_signed_in_is_login_required_and_drops_the_cached_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    dropped: list[Path | None] = []
    monkeypatch.setattr(
        "thoth.adapters.models.claude_code.invalidate_claude_code_status",
        lambda workspace=None: dropped.append(workspace),
    )

    async def spawn(_argv: tuple[str, ...], _env: Mapping[str, str], _cwd: Path) -> FakeProcess:
        return FakeProcess(NOT_LOGGED_IN, exit_code=exit_code)

    workspace = tmp_path / "workspace"
    model = ClaudeCodeModel(
        workspace,
        model="sonnet",
        executable=_binary(tmp_path),
        process_factory=spawn,
        status_probe=_connected,
    )
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_LOGIN_REQUIRED"):
        await model.structured(_request_with_selection())
    assert dropped == [workspace]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        {"type": "result", "is_error": True, "result": "Rate limit reached"},
        {"type": "result", "is_error": False, "result": "Please run /login"},
        {"type": "assistant", "is_error": True, "result": "Not logged in"},
        {},
    ],
)
async def test_any_other_failure_keeps_the_existing_code_and_the_cached_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: dict[str, object]
) -> None:
    dropped: list[Path | None] = []
    monkeypatch.setattr(
        "thoth.adapters.models.claude_code.invalidate_claude_code_status",
        lambda workspace=None: dropped.append(workspace),
    )

    async def spawn(_argv: tuple[str, ...], _env: Mapping[str, str], _cwd: Path) -> FakeProcess:
        return FakeProcess(output, exit_code=1)

    model = ClaudeCodeModel(
        tmp_path / "workspace",
        model="sonnet",
        executable=_binary(tmp_path),
        process_factory=spawn,
        status_probe=_connected,
    )
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_EXECUTION_UNAVAILABLE"):
        await model.structured(_request_with_selection())
    assert dropped == []


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


def _status_runner(
    auth_stdout: str, *, auth_code: int = 0, captured: list[tuple[str, ...]] | None = None
) -> Callable[[tuple[str, ...], Mapping[str, str]], subprocess.CompletedProcess[str]]:
    def runner(argv: tuple[str, ...], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        if captured is not None:
            captured.append(argv)
        assert "CLAUDE_CONFIG_DIR" in env
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, "2.1.284 (Claude Code)", "")
        return subprocess.CompletedProcess(argv, auth_code, auth_stdout, "")

    return runner


def test_status_reads_json_and_marks_a_subscription_login_eligible(tmp_path: Path) -> None:
    binary = _binary(tmp_path)
    captured: list[tuple[str, ...]] = []
    runner = _status_runner(
        '{"loggedIn":true,"authMethod":"claude.ai subscription"}', captured=captured
    )
    status = claude_code_status(tmp_path / "workspace", executable=binary, runner=runner)
    assert status["connection_state"] == "EXECUTION_UNVERIFIED"
    assert status["connected"] is True and status["execution_verified"] is False
    assert status["execution_eligible"] is True
    assert status["configured_auth_mode"] == "PROFILE"
    assert status["detected_auth_mode"] == "SUBSCRIPTION"
    assert len(captured) == 2 and all(argv[0] == str(binary) for argv in captured)
    assert captured[1][1:] == ("auth", "status", "--json")
    catalog = ClaudeCodeCatalog(tmp_path, status=lambda _root: status)
    options = catalog.options()
    assert [option.model for option in options] == ["sonnet", "opus", "haiku"]
    assert {option.provider for option in options} == {"claude-code"}


@pytest.mark.parametrize(
    ("payload", "detected"),
    [
        ('{"loggedIn":true,"authMethod":"console api key"}', "API_KEY"),
        ('{"loggedIn":true,"authMethod":"bedrock"}', "CLOUD"),
        ('{"loggedIn":true}', "UNKNOWN"),
    ],
)
def test_a_login_that_is_not_a_subscription_is_connected_but_not_eligible(
    tmp_path: Path, payload: str, detected: str
) -> None:
    status = claude_code_status(
        tmp_path / "workspace", executable=_binary(tmp_path), runner=_status_runner(payload)
    )
    assert status["connected"] is True
    assert status["detected_auth_mode"] == detected
    assert status["execution_eligible"] is False
    assert ClaudeCodeCatalog(tmp_path, status=lambda _root: status).options() == ()


def test_logged_out_and_unsupported_status_are_not_eligible(tmp_path: Path) -> None:
    logged_out = claude_code_status(
        tmp_path / "workspace",
        executable=_binary(tmp_path),
        runner=_status_runner('{"loggedIn":false}', auth_code=1),
    )
    assert logged_out["connection_state"] == "LOGIN_REQUIRED"
    assert logged_out["execution_eligible"] is False

    def old_version(
        argv: tuple[str, ...], env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "2.0.1 (Claude Code)", "")

    old = claude_code_status(
        tmp_path / "workspace", executable=_binary(tmp_path), runner=old_version
    )
    assert old["reason_code"] == "CLAUDE_CODE_CAPABILITY_UNSUPPORTED"
    assert old["execution_eligible"] is False


def test_factory_holds_when_the_status_is_not_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.adapters.models import claude_code
    from thoth.adapters.models.claude_code import claude_code_model_factory

    def not_eligible(_workspace: Path) -> dict[str, object]:
        return {"connected": True, "execution_eligible": False}

    monkeypatch.setattr(claude_code, "cached_claude_code_status", not_eligible)
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_EXECUTION_UNVERIFIED"):
        claude_code_model_factory(tmp_path, "sonnet")


def test_status_cache_probes_once_per_window_and_can_be_invalidated(tmp_path: Path) -> None:
    now = [100.0]
    calls: list[Path] = []

    def probe(workspace: Path) -> dict[str, object]:
        calls.append(workspace)
        return {"connected": bool(len(calls) % 2)}

    cache = ClaudeCodeStatusCache(ttl_seconds=30.0, clock=lambda: now[0])
    first = cache.get(tmp_path, probe)
    now[0] += 29.0
    assert cache.get(tmp_path, probe) == first and len(calls) == 1
    now[0] += 2.0
    assert cache.get(tmp_path, probe) != first and len(calls) == 2
    cache.invalidate(tmp_path)
    cache.get(tmp_path, probe)
    assert len(calls) == 3
    other = tmp_path / "other"
    cache.get(other, probe)
    assert len(calls) == 4


def test_unimplemented_explicit_auth_mode_holds_without_fallback(tmp_path: Path) -> None:
    status = claude_code_status(tmp_path, auth_mode="EXPLICIT_API_KEY")
    assert status["reason_code"] == "CLAUDE_CODE_AUTH_MODE_UNSUPPORTED"
    assert status["connected"] is False
    assert ClaudeCodeCatalog(tmp_path, auth_mode="EXPLICIT_CLOUD").options() == ()


class UsageBoundary:
    """Minimal research boundary that records what the model reports."""

    def __init__(self) -> None:
        self.reserved: list[tuple[str, ModelControlCapability]] = []
        self.usage: list[tuple[int, int | None, int | None, str]] = []

    def check(self) -> None:
        pass

    def reserve(self, payload_bytes: int, output_tokens: int = 0) -> None:
        pass

    def transport(self, payload_bytes: int) -> None:
        pass

    def call_timeout(self) -> float:
        return 5.0

    def owns_attempt(self) -> bool:
        return True

    def new_model_call(self) -> str:
        return "call"

    def reserve_dispatch(
        self,
        dispatch_id: str,
        payload: bytes,
        output_tokens: int,
        capability: ModelControlCapability,
    ) -> None:
        # The real boundary rejects an UNVERIFIED control before any process starts.
        if capability.output_control == "UNVERIFIED" or capability.native_tools != "NONE":
            raise ModelExecutionHold("MODEL_TRANSPORT_CONTROLS_UNVERIFIED")
        self.reserved.append((dispatch_id, capability))

    def record_usage(
        self,
        dispatch_id: str,
        received_bytes: int,
        input_tokens: int | None,
        output_tokens: int | None,
        remote_stop: str,
        response_id: str | None,
        observation: object | None = None,
        retry_of_dispatch_id: str | None = None,
        cached_input_tokens: int | None = None,
    ) -> None:
        self.usage.append((received_bytes, input_tokens, output_tokens, remote_stop))


async def _run_with_boundary(
    tmp_path: Path, output: dict[str, object], boundary: UsageBoundary
) -> ModelResult[DemoOutput]:
    async def spawn(_argv: tuple[str, ...], _env: Mapping[str, str], _cwd: Path) -> FakeProcess:
        return FakeProcess(output)

    model = ClaudeCodeModel(
        tmp_path / "workspace",
        model="sonnet",
        executable=_binary(tmp_path),
        process_factory=spawn,
        status_probe=_connected,
    )
    work = ResearchWork(
        RevisionRef(
            project_id="project:synthetic",
            entity_type="THREAD",
            entity_id="request:synthetic",
            revision_id="revision:synthetic",
            revision_digest="0" * 64,
            schema_version="2.0.0",
        ),
        "Synthetic question",
        boundary,
    )
    token = research_work.set(work)
    try:
        return await model.structured(_request_with_selection())
    finally:
        research_work.reset(token)


def test_control_is_observation_only_like_the_other_account_routes() -> None:
    capability = ClaudeCodeModel.control_capability
    assert capability.capability_id == "claude-code-cli-observed-v2"
    assert capability.output_control == "OBSERVATION_ONLY"
    assert capability.native_tools == "NONE"
    assert capability.remote_stop_guaranteed is False


@pytest.mark.asyncio
async def test_a_research_run_is_admitted_and_reports_the_tokens_the_cli_returned(
    tmp_path: Path,
) -> None:
    boundary = UsageBoundary()
    result = await _run_with_boundary(
        tmp_path,
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": {"label": "synthetic", "count": 2},
            "usage": {"input_tokens": 11, "output_tokens": 7},
        },
        boundary,
    )
    assert result.output.label == "synthetic"
    assert len(boundary.reserved) == 1 and len(result.dispatch_ids) == 1
    assert boundary.reserved[0][1].output_control == "OBSERVATION_ONLY"
    assert [(item[1], item[2], item[3]) for item in boundary.usage] == [(11, 7, "UNKNOWN")]


@pytest.mark.asyncio
async def test_missing_or_malformed_usage_is_recorded_as_unknown_not_zero(tmp_path: Path) -> None:
    boundary = UsageBoundary()
    await _run_with_boundary(
        tmp_path,
        {
            "type": "result",
            "subtype": "success",
            "structured_output": {"label": "synthetic", "count": 2},
            "usage": {"input_tokens": "many", "output_tokens": True},
        },
        boundary,
    )
    assert [(item[1], item[2]) for item in boundary.usage] == [(None, None)]


@pytest.mark.asyncio
async def test_a_tool_tainted_answer_is_still_rejected_but_its_usage_is_recorded(
    tmp_path: Path,
) -> None:
    boundary = UsageBoundary()
    with pytest.raises(ClaudeCodeUnavailable, match="CLAUDE_CODE_STRUCTURED_OUTPUT_UNAVAILABLE"):
        await _run_with_boundary(
            tmp_path,
            {
                "type": "result",
                "subtype": "success",
                "structured_output": {"label": "x", "count": 1},
                "tool_uses": ["Bash"],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
            boundary,
        )
    assert [(item[1], item[2]) for item in boundary.usage] == [(5, 3)]


def test_the_print_command_line_is_built_in_one_place() -> None:
    from thoth.adapters.models.claude_code import claude_print_argv

    argv = claude_print_argv(Path("claude.exe"), "sonnet", "high", '{"type":"object"}')
    assert argv[:2] == ("claude.exe", "--print")
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--json-schema") + 1] == '{"type":"object"}'
    assert "--effort" not in claude_print_argv(Path("claude.exe"), "sonnet", None, "{}")
