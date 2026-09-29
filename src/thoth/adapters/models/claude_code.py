"""Official Claude Code CLI lane, isolated from THOTH API-key credentials.

The published executable owns sign-in and refresh. THOTH neither reads its
credential store nor sends Claude subscription tokens to an API/Agent SDK.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ValidationError

from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.adapters.models.reference_schema import constrain_span_references
from thoth.domain.canonical import canonical_payload, domain_digest, model_digest
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelControlCapability
from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.domain.research_execution import (
    check_research_boundary,
    research_work,
    reserve_model_dispatch,
)
from thoth.ports.model import ModelExecutionHold, ModelPort
from thoth.ports.model_catalog import ModelCatalogPort

TModel = TypeVar("TModel", bound=BaseModel)
ClaudeAuthMode = Literal["PROFILE", "EXPLICIT_API_KEY", "EXPLICIT_CLOUD"]
_MIN_VERSION = (2, 1, 259)
_MODELS = ("sonnet", "opus", "haiku")
_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_MAX_SCHEMA_CHARS = 16000
_MAX_WIRE_BYTES = 4_000_000


class ClaudeCodeUnavailable(ModelExecutionHold):
    """Typed fail-closed reason; no CLI diagnostic or credential is exposed."""


class ClaudeProcess(Protocol):
    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]: ...
    def kill(self) -> None: ...
    async def wait(self) -> int: ...


ProcessFactory = Callable[[tuple[str, ...], Mapping[str, str], Path], Awaitable[ClaudeProcess]]
StatusRunner = Callable[[tuple[str, ...], Mapping[str, str]], subprocess.CompletedProcess[str]]


def claude_profile_dir(workspace: Path) -> Path:
    return workspace.resolve() / "model-profiles" / "claude-code"


def _child_environment(workspace: Path) -> dict[str, str]:
    # A narrow inherited environment prevents ambient API keys or OAuth tokens
    # from silently changing this binary's user-owned subscription profile.
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "APPDATA",
        "LOCALAPPDATA",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["CLAUDE_CONFIG_DIR"] = str(claude_profile_dir(workspace))
    return env


def resolve_claude_executable(explicit: Path | None = None) -> Path:
    raw = str(explicit) if explicit is not None else shutil.which("claude")
    if not raw:
        raise ClaudeCodeUnavailable("CLAUDE_CODE_NOT_INSTALLED")
    executable = Path(raw).resolve()
    if not executable.is_file():
        raise ClaudeCodeUnavailable("CLAUDE_CODE_NOT_INSTALLED")
    # Windows command shims are scripts. The route requires the official native binary.
    if sys.platform == "win32" and executable.suffix.lower() != ".exe":
        raise ClaudeCodeUnavailable("CLAUDE_CODE_NATIVE_BINARY_REQUIRED")
    if sys.platform != "win32" and not os.access(executable, os.X_OK):
        raise ClaudeCodeUnavailable("CLAUDE_CODE_NOT_EXECUTABLE")
    return executable


def _status_run(argv: tuple[str, ...], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, env=dict(env), capture_output=True, text=True, check=False, timeout=8
    )


def _version(raw: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?:^|\s)(\d+)\.(\d+)\.(\d+)(?:\s|$)", raw.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def claude_code_status(
    workspace: Path,
    *,
    executable: Path | None = None,
    runner: StatusRunner = _status_run,
    auth_mode: ClaudeAuthMode = "PROFILE",
) -> dict[str, object]:
    """Read only the official binary's status; do not inspect auth files."""
    if auth_mode != "PROFILE":
        return {
            "provider": "claude-code",
            "connection_state": "UNAVAILABLE",
            "reason_code": "CLAUDE_CODE_AUTH_MODE_UNSUPPORTED",
            "connected": False,
            "profile_mode": "THOTH_ISOLATED",
            "configured_auth_mode": auth_mode,
            "detected_auth_mode": "UNKNOWN",
            "auth_source_match": None,
            "execution_eligible": False,
            "execution_verified": False,
        }
    try:
        binary = resolve_claude_executable(executable)
        env = _child_environment(workspace)
        version = runner((str(binary), "--version"), env)
        parsed_version = _version(version.stdout) if version.returncode == 0 else None
        if parsed_version is None or parsed_version < _MIN_VERSION:
            reason = "CLAUDE_CODE_CAPABILITY_UNSUPPORTED"
            return {
                "provider": "claude-code",
                "connection_state": "UNAVAILABLE",
                "reason_code": reason,
                "connected": False,
                "profile_mode": "THOTH_ISOLATED",
                "configured_auth_mode": auth_mode,
                "detected_auth_mode": "UNKNOWN",
                "auth_source_match": None,
                "execution_eligible": False,
                "execution_verified": False,
            }
        status = runner((str(binary), "auth", "status"), env)
        detected = "UNKNOWN"
        if status.returncode == 1:
            state, reason, connected = "LOGIN_REQUIRED", "CLAUDE_CODE_LOGIN_REQUIRED", False
        elif status.returncode != 0:
            state, reason, connected = "ERROR", "CLAUDE_CODE_STATUS_UNAVAILABLE", False
        else:
            try:
                raw_payload: object = json.loads(status.stdout)
            except (ValueError, TypeError):
                raw_payload = None
            payload = (
                cast(dict[str, object], raw_payload) if isinstance(raw_payload, dict) else None
            )
            method = None if payload is None else payload.get("authMethod")
            if isinstance(method, str):
                lowered = method.lower()
                if "claude.ai" in lowered or "subscription" in lowered:
                    detected = "SUBSCRIPTION"
                elif any(name in lowered for name in ("bedrock", "vertex", "foundry")):
                    detected = "CLOUD"
                elif "api" in lowered or "console" in lowered:
                    detected = "API_KEY"
            if payload is not None and payload.get("loggedIn") is True:
                state, reason, connected = (
                    "EXECUTION_UNVERIFIED",
                    "CLAUDE_CODE_EXECUTION_UNVERIFIED",
                    True,
                )
            elif payload is not None and payload.get("loggedIn") is False:
                state, reason, connected = "LOGIN_REQUIRED", "CLAUDE_CODE_LOGIN_REQUIRED", False
            else:
                state, reason, connected = "ERROR", "CLAUDE_CODE_STATUS_UNAVAILABLE", False
        return {
            "provider": "claude-code",
            "connection_state": state,
            "reason_code": reason,
            "connected": connected,
            "profile_mode": "THOTH_ISOLATED",
            "configured_auth_mode": auth_mode,
            "detected_auth_mode": detected,
            "auth_source_match": None,
            "execution_eligible": False,
            "execution_verified": False,
        }
    except ClaudeCodeUnavailable as exc:
        return {
            "provider": "claude-code",
            "connection_state": "UNAVAILABLE",
            "reason_code": str(exc),
            "connected": False,
            "profile_mode": "THOTH_ISOLATED",
            "configured_auth_mode": auth_mode,
            "detected_auth_mode": "UNKNOWN",
            "auth_source_match": None,
            "execution_eligible": False,
            "execution_verified": False,
        }
    except (OSError, subprocess.TimeoutExpired):
        return {
            "provider": "claude-code",
            "connection_state": "ERROR",
            "reason_code": "CLAUDE_CODE_STATUS_UNAVAILABLE",
            "connected": False,
            "profile_mode": "THOTH_ISOLATED",
            "configured_auth_mode": auth_mode,
            "detected_auth_mode": "UNKNOWN",
            "auth_source_match": None,
            "execution_eligible": False,
            "execution_verified": False,
        }


def claude_code_login_guidance(workspace: Path) -> dict[str, object]:
    """Return local terminal guidance; never start login inside an HTTP request."""
    status = claude_code_status(workspace)
    reason = status.get("reason_code")
    if status.get("connected") is True or status.get("connection_state") == "UNAVAILABLE":
        return {
            "started": False,
            "reason_code": reason,
            "profile_mode": "THOTH_ISOLATED",
        }
    return {
        "started": False,
        "reason_code": "CLAUDE_CODE_USER_LOGIN_REQUIRED",
        "profile_mode": "THOTH_ISOLATED",
        "next_step": (
            "Run `thoth claude-code-login --workspace <selected-workspace>` in a terminal. "
            "The official binary offers its own supported sign-in methods."
        ),
    }


def run_claude_code_login(workspace: Path, *, console: bool = False) -> int:
    """User-invoked interactive sign-in through the unmodified binary."""
    binary = resolve_claude_executable()
    argv = (
        (str(binary), "auth", "login", "--console") if console else (str(binary), "auth", "login")
    )
    return subprocess.run(argv, env=_child_environment(workspace), check=False).returncode


class ClaudeCodeCatalog(ModelCatalogPort):
    def __init__(
        self,
        workspace: Path,
        *,
        status: Callable[[Path], dict[str, object]] = claude_code_status,
        auth_mode: ClaudeAuthMode = "PROFILE",
        execution_eligible: bool = False,
    ) -> None:
        self._workspace, self._status, self._auth_mode = workspace, status, auth_mode
        self._execution_eligible = execution_eligible

    def defaults(self) -> ModelSelection:
        return ModelSelection(provider="claude-code")

    def options(self) -> tuple[ModelOption, ...]:
        if self._auth_mode != "PROFILE" or not self._execution_eligible:
            return ()
        state = self._status(self._workspace)
        if state.get("connected") is not True or state.get("execution_eligible") is not True:
            return ()
        return tuple(
            ModelOption(
                provider="claude-code",
                model=name,
                reasoning_efforts=("low", "medium", "high"),
                capability_source="claude-code-cli-documented-unverified-v1",
            )
            for name in _MODELS
        )


async def _finish_shielded[T](task: asyncio.Task[T]) -> T:
    """Finish cleanup ownership even if more cancellation arrives meanwhile."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


async def spawn_claude_process(
    argv: tuple[str, ...], env: Mapping[str, str], cwd: Path
) -> ClaudeProcess:
    if sys.platform == "win32":
        from thoth.adapters.models.windows_process_job import spawn_windows_job

        pending = asyncio.create_task(asyncio.to_thread(spawn_windows_job, argv, env, cwd))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError as cancelled:
            try:
                process = await _finish_shielded(pending)
            except Exception:
                # The native spawn failed and its own failure path owns cleanup.
                raise cancelled from None
            cleanup = asyncio.create_task(_stop_process_tree(process))
            if not await _finish_shielded(cleanup):
                raise ClaudeCodeUnavailable("CLAUDE_CODE_CLEANUP_UNCONFIRMED") from cancelled
            raise
    return await asyncio.create_subprocess_exec(
        *argv,
        env=dict(env),
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=sys.platform != "win32",
    )


async def _stop_process_tree(process: ClaudeProcess) -> bool:
    """Stop local descendants; provider-side remote stop remains UNKNOWN."""
    if sys.platform == "win32":
        from thoth.adapters.models.windows_process_job import WindowsJobProcess

        if not isinstance(process, WindowsJobProcess):
            return False
        process.kill()
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    if sys.platform != "win32" and process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    try:
        await asyncio.wait_for(process.wait(), 3.0)
    except TimeoutError:
        return False
    return process.returncode is not None


class ClaudeCodeModel(ModelPort):
    control_capability = ModelControlCapability(
        capability_id="claude-code-cli-no-tools-pending-dispatch-v1",
        # One CLI process may make more than one physical provider request.
        # Keep normal ResearchModel fail-closed until dispatch accounting and
        # Windows process-tree supervision are independently accepted.
        output_control="UNVERIFIED",
        native_tools="NONE",
        cancellation="LOCAL_TRANSPORT",
        owns_serialization=True,
    )

    def __init__(
        self,
        workspace: Path,
        *,
        model: str | None,
        executable: Path | None = None,
        process_factory: ProcessFactory = spawn_claude_process,
        stop_process_tree: Callable[[ClaudeProcess], Awaitable[bool]] = _stop_process_tree,
        timeout_seconds: float = 120.0,
        status_probe: Callable[..., dict[str, object]] = claude_code_status,
        auth_mode: ClaudeAuthMode = "PROFILE",
    ) -> None:
        if auth_mode != "PROFILE":
            raise ClaudeCodeUnavailable("CLAUDE_CODE_AUTH_MODE_UNSUPPORTED")
        if model not in _MODELS:
            raise ClaudeCodeUnavailable("CLAUDE_CODE_MODEL_UNSUPPORTED")
        self._workspace = workspace.resolve()
        self._model = model
        self._executable = resolve_claude_executable(executable)
        state = status_probe(self._workspace, executable=self._executable)
        if state.get("connected") is not True:
            raise ClaudeCodeUnavailable(
                str(state.get("reason_code") or "CLAUDE_CODE_STATUS_UNAVAILABLE")
            )
        self._process_factory = process_factory
        self._stop_process_tree = stop_process_tree
        self._timeout_seconds = timeout_seconds

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        check_research_boundary()
        selected = request.model_settings
        if selected is not None and (
            selected.provider != "claude-code" or selected.model != self._model
        ):
            raise ClaudeCodeUnavailable("MODEL_SETTINGS_BINDING_MISMATCH")
        effort = None if selected is None else selected.reasoning_effort
        if effort is not None and effort not in _EFFORTS:
            raise ClaudeCodeUnavailable("CLAUDE_CODE_EFFORT_UNSUPPORTED")
        if request.max_output_tokens <= 0:
            raise ClaudeCodeUnavailable("CLAUDE_CODE_OUTPUT_LIMIT_INVALID")
        schema = constrain_span_references(
            request.output_model.model_json_schema(),
            tuple(span.span_id for span in request.context_pack.evidence),
            request.context_pack.research_context,
        )
        schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        if len(schema_json) > _MAX_SCHEMA_CHARS:
            raise ClaudeCodeUnavailable("CLAUDE_CODE_SCHEMA_TOO_LARGE")
        prompt = (
            OpenAIResponsesModel.instructions(request)
            + "\n"
            + OpenAIResponsesModel.input_envelope(request)
        )
        payload = canonical_payload(
            {
                "prompt": prompt,
                "schema": schema,
                "model": self._model,
                "effort": effort,
                "output_limit": request.max_output_tokens,
            }
        )
        wire = payload
        dispatch_id = reserve_model_dispatch(
            wire, request.max_output_tokens, self.control_capability
        )
        argv = [
            str(self._executable),
            "--print",
            "--safe-mode",
            "--tools",
            "",
            "--disallowedTools",
            "*",
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-chrome",
            "--no-session-persistence",
            "--max-turns",
            "1",
            "--model",
            self._model,
            "--output-format",
            "json",
            "--json-schema",
            schema_json,
        ]
        if effort is not None:
            argv.extend(("--effort", effort))
        env = _child_environment(self._workspace)
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(request.max_output_tokens)
        # The official variable counts attempts (default five), not retries.
        # One structured attempt still does not prove one physical API request.
        env["MAX_STRUCTURED_OUTPUT_RETRIES"] = "1"
        if effort is not None:
            env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
        work = research_work.get()
        timeout = (
            self._timeout_seconds
            if work is None
            else min(self._timeout_seconds, work.boundary.call_timeout() or self._timeout_seconds)
        )
        with tempfile.TemporaryDirectory(prefix="thoth-claude-model-") as temporary:
            process = await self._process_factory(tuple(argv), env, Path(temporary))
            try:
                stdout, _stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode()), timeout
                )
            except (TimeoutError, asyncio.CancelledError) as exc:
                stopped = await self._stop_process_tree(process)
                if not stopped:
                    raise ClaudeCodeUnavailable("CLAUDE_CODE_CLEANUP_UNCONFIRMED") from exc
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise ClaudeCodeUnavailable("CLAUDE_CODE_TIMEOUT_REMOTE_STOP_UNKNOWN") from exc
        if len(stdout) > _MAX_WIRE_BYTES:
            raise ClaudeCodeUnavailable("CLAUDE_CODE_RESPONSE_TOO_LARGE")
        if work is not None:
            work.boundary.record_usage(dispatch_id, len(stdout), None, None, "UNKNOWN", None)
        if process.returncode != 0:
            raise ClaudeCodeUnavailable("CLAUDE_CODE_EXECUTION_UNAVAILABLE")
        try:
            raw_envelope: object = json.loads(stdout)
            envelope = (
                cast(dict[str, object], raw_envelope) if isinstance(raw_envelope, dict) else None
            )
            if (
                envelope is None
                or envelope.get("type") != "result"
                or envelope.get("subtype") != "success"
                or envelope.get("is_error") is True
                or envelope.get("tool_uses")
                or envelope.get("tool_use")
            ):
                raise ValueError("invalid envelope")
            raw_output = envelope.get("structured_output")
            if not isinstance(raw_output, dict):
                raise ValueError("structured output absent")
            output = cast(dict[str, object], raw_output)
            parsed = request.output_model.model_validate(output)
        except (ValueError, TypeError, ValidationError) as exc:
            raise ClaudeCodeUnavailable("CLAUDE_CODE_STRUCTURED_OUTPUT_UNAVAILABLE") from exc
        return ModelResult(
            output=parsed,
            model_id=f"claude-code/{self._model}",
            prompt_version=request.prompt_version,
            scripted=False,
            dispatch_ids=(dispatch_id,),
            input_digest=domain_digest("MODEL_PROMPT_INPUT", "2.0.0", payload),
            output_digest=model_digest(
                "MODEL_OUTPUT", cast(BaseModel, parsed), schema_version="1.0.0"
            ),
        )


def claude_code_model_factory(
    workspace: Path,
    model: str | None,
    *,
    auth_mode: ClaudeAuthMode = "PROFILE",
    explicit_environment: Mapping[str, str] | None = None,
    execution_eligible: bool = False,
) -> ModelPort:
    if auth_mode != "PROFILE" or explicit_environment is not None:
        raise ClaudeCodeUnavailable("CLAUDE_CODE_AUTH_MODE_UNSUPPORTED")
    if not execution_eligible:
        raise ClaudeCodeUnavailable("CLAUDE_CODE_EXECUTION_UNVERIFIED")
    if claude_code_status(workspace).get("execution_eligible") is not True:
        raise ClaudeCodeUnavailable("CLAUDE_CODE_EXECUTION_UNVERIFIED")
    return ClaudeCodeModel(workspace, model=model)
