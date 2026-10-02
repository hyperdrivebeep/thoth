"""Official Claude Code CLI lane, isolated from THOTH API-key credentials.

The published executable owns sign-in and refresh. THOTH neither reads its
credential store nor sends Claude subscription tokens to an API/Agent SDK.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from pathlib import Path, PureWindowsPath
from typing import Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ValidationError

from thoth.adapters.models.codex_oauth import (
    constrain_action_families,
    prompt_envelope,
    strict_output_schema,
)
from thoth.adapters.models.reference_schema import (
    apply_hypothesis_review_contract,
    constrain_span_references,
)
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
    # THOTH must never make a shared (global) Claude Code update itself as a side effect.
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


_BATCH_SUFFIXES = frozenset({".bat", ".cmd"})
_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"
# npm installs Claude Code on Windows as a cmd shim around its native binary:
#   "%dp0%\node_modules\@anthropic-ai\claude-code\bin\claude.exe"   %*
# Older shims spell the variable %~dp0.
_CMD_SHIM_TARGET = re.compile(r'"%~?dp0%?\\([^"%]+?\.exe)"', re.IGNORECASE)


def _native_target_of_shim(shim: Path) -> Path | None:
    """The native .exe an npm cmd shim wraps; the shim itself cannot be spawned without a shell."""
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _CMD_SHIM_TARGET.search(text)
    if match is None:
        return None
    target = shim.parent.joinpath(*PureWindowsPath(match[1]).parts)
    return target if target.is_file() else None


def _find_on_path(env: Mapping[str, str], platform: str) -> tuple[Path | None, bool]:
    """First usable `claude` on PATH, and whether a shim without a native target was skipped."""
    windows = platform == "win32"
    if windows:
        extensions = [
            item.lower() for item in env.get("PATHEXT", _DEFAULT_PATHEXT).split(";") if item
        ]
        names = [f"claude{extension}" for extension in extensions]
    else:
        names = ["claude"]
    skipped_shim = False
    for entry in env.get("PATH", "").split(";" if windows else os.pathsep):
        directory = entry.strip().strip('"')
        if not directory:
            continue
        for name in names:
            candidate = Path(directory) / name
            if not candidate.is_file():
                continue
            if not windows or candidate.suffix.lower() not in _BATCH_SUFFIXES:
                return candidate, skipped_shim
            target = _native_target_of_shim(candidate)
            if target is not None:
                return target, skipped_shim
            skipped_shim = True
    return None, skipped_shim


def thoth_claude_code_executable(values: Mapping[str, str]) -> Path | None:
    """Where the THOTH-owned install puts the official binary (Windows tools folder)."""
    local = values.get("LOCALAPPDATA")
    if not local:
        return None
    tools = Path(local) / "THOTH" / "tools" / "claude-code"
    return tools / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"


def _fallback_locations(values: Mapping[str, str], windows: bool) -> tuple[Path, ...]:
    """Where a THOTH-installed or officially installed binary lives when PATH has none."""
    home = Path(values.get("USERPROFILE") or values.get("HOME") or Path.home())
    official = home / ".local" / "bin" / ("claude.exe" if windows else "claude")
    thoth = thoth_claude_code_executable(values) if windows else None
    return (official,) if thoth is None else (thoth, official)


def resolve_claude_executable(
    explicit: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Find the official Claude Code binary.

    Order: explicit path, THOTH's own tools folder, PATH, then the official installer folder. A
    global claude (which other programs use) never overrides the copy THOTH installed itself.
    """
    values = os.environ if env is None else env
    system = sys.platform if platform is None else platform
    windows = system == "win32"
    skipped_shim = False
    found: Path | None = None
    if explicit is not None:
        found = explicit
        if windows and explicit.suffix.lower() in _BATCH_SUFFIXES and explicit.is_file():
            found = _native_target_of_shim(explicit)
            if found is None:
                raise ClaudeCodeUnavailable("CLAUDE_CODE_NATIVE_BINARY_REQUIRED")
    else:
        thoth = thoth_claude_code_executable(values) if windows else None
        found = thoth if thoth is not None and thoth.is_file() else None
        if found is None:
            found, skipped_shim = _find_on_path(values, system)
        if found is None:
            found = next(
                (path for path in _fallback_locations(values, windows) if path.is_file()), None
            )
    if found is None:
        raise ClaudeCodeUnavailable(
            "CLAUDE_CODE_NATIVE_BINARY_REQUIRED" if skipped_shim else "CLAUDE_CODE_NOT_INSTALLED"
        )
    executable = found.resolve()
    if not executable.is_file():
        raise ClaudeCodeUnavailable("CLAUDE_CODE_NOT_INSTALLED")
    # Any other Windows script shim is not spawnable; the route needs the official native binary.
    if windows and executable.suffix.lower() != ".exe":
        raise ClaudeCodeUnavailable("CLAUDE_CODE_NATIVE_BINARY_REQUIRED")
    if not windows and not os.access(executable, os.X_OK):
        raise ClaudeCodeUnavailable("CLAUDE_CODE_NOT_EXECUTABLE")
    return executable


def _status_run(argv: tuple[str, ...], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, env=dict(env), capture_output=True, text=True, check=False, timeout=8
    )


def _version(raw: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?:^|\s)(\d+)\.(\d+)\.(\d+)(?:\s|$)", raw.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _detect_auth_mode(payload: dict[str, object] | None) -> str:
    """Classify the login the official binary reports; unknown shapes stay UNKNOWN."""
    if payload is None:
        return "UNKNOWN"
    method = payload.get("authMethod")
    lowered = method.lower() if isinstance(method, str) else ""
    if any(name in lowered for name in ("bedrock", "vertex", "foundry")):
        return "CLOUD"
    if any(name in lowered for name in ("api_key", "apikey", "api key", "console")):
        return "API_KEY"
    subscription_type = payload.get("subscriptionType")
    if any(name in lowered for name in ("claude.ai", "claude_ai", "claudeai", "subscription")) or (
        isinstance(subscription_type, str) and subscription_type != ""
    ):
        return "SUBSCRIPTION"
    return "UNKNOWN"


def claude_code_status(
    workspace: Path,
    *,
    executable: Path | None = None,
    runner: StatusRunner | None = None,
    auth_mode: ClaudeAuthMode = "PROFILE",
) -> dict[str, object]:
    """Read only the official binary's status; do not inspect auth files."""
    runner = runner or _status_run
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
        status = runner((str(binary), "auth", "status", "--json"), env)
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
            detected = _detect_auth_mode(payload)
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
            # Only a subscription login is used by this route. Eligible means the local
            # conditions hold; a real answer is still unverified until a run succeeds.
            "execution_eligible": connected and detected == "SUBSCRIPTION",
            "execution_verified": False,
            "cli_version": ".".join(str(part) for part in parsed_version),
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


class ClaudeCodeStatusCache:
    """Share one binary status probe per workspace for a short window.

    Each probe starts the official executable twice (version, then auth status), so the
    account list, catalog and readiness checks must not probe on every call.
    """

    def __init__(
        self, ttl_seconds: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl, self._clock = ttl_seconds, clock
        self._lock = threading.Lock()
        self._entries: dict[Path, tuple[float, dict[str, object]]] = {}

    def get(self, workspace: Path, probe: Callable[[Path], dict[str, object]]) -> dict[str, object]:
        key = workspace.resolve()
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and self._clock() - cached[0] < self._ttl:
                return dict(cached[1])
            value = probe(workspace)
            self._entries[key] = (self._clock(), value)
            return dict(value)

    def invalidate(self, workspace: Path | None = None) -> None:
        with self._lock:
            if workspace is None:
                self._entries.clear()
            else:
                self._entries.pop(workspace.resolve(), None)


_STATUS_CACHE = ClaudeCodeStatusCache()


def cached_claude_code_status(workspace: Path) -> dict[str, object]:
    return _STATUS_CACHE.get(workspace, lambda root: claude_code_status(root))


def invalidate_claude_code_status(workspace: Path | None = None) -> None:
    """Drop the cached status after login, cancel or logout changed it."""
    _STATUS_CACHE.invalidate(workspace)


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
        status: Callable[[Path], dict[str, object]] | None = None,
        auth_mode: ClaudeAuthMode = "PROFILE",
    ) -> None:
        self._workspace, self._status, self._auth_mode = workspace, status, auth_mode

    def defaults(self) -> ModelSelection:
        return ModelSelection(provider="claude-code")

    def options(self) -> tuple[ModelOption, ...]:
        if self._auth_mode != "PROFILE":
            return ()
        state = (self._status or cached_claude_code_status)(self._workspace)
        if state.get("connected") is not True or state.get("execution_eligible") is not True:
            return ()
        version = state.get("cli_version")
        suffix = f" · Claude Code {version}" if isinstance(version, str) and version else ""
        return tuple(
            ModelOption(
                provider="claude-code",
                model=name,
                reasoning_efforts=("low", "medium", "high"),
                capability_source="claude-code-cli-observed-v2",
                # An alias: which model it resolves to is not known until a real request.
                label=f"최신 {name.capitalize()}(별칭){suffix}",
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


# Claude Code 2.1.284 no longer lists --max-turns in --help. Whether the executable still accepts
# it is decided by the first real run (plan step P2-0); dropping it is a change to this tuple only.
_MAX_TURNS_ARGV: tuple[str, ...] = ("--max-turns", "1")


def claude_print_argv(
    executable: Path, model: str, effort: str | None, schema_json: str
) -> tuple[str, ...]:
    """The one place that spells the no-tools, single-answer command line."""
    argv = [
        str(executable),
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
        *_MAX_TURNS_ARGV,
        "--model",
        model,
        "--output-format",
        "json",
        "--json-schema",
        schema_json,
    ]
    if effort is not None:
        argv.extend(("--effort", effort))
    return tuple(argv)


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _reported_usage(stdout: bytes) -> tuple[int | None, int | None]:
    """Token counts from the JSON envelope; anything else stays unknown."""
    try:
        envelope: object = json.loads(stdout)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(envelope, dict):
        return None, None
    usage = cast(dict[str, object], envelope).get("usage")
    if not isinstance(usage, dict):
        return None, None
    counts = cast(dict[str, object], usage)
    return _token_count(counts.get("input_tokens")), _token_count(counts.get("output_tokens"))


class ClaudeCodeModel(ModelPort):
    control_capability = ModelControlCapability(
        capability_id="claude-code-cli-observed-v2",
        # Same rule as the Codex and xAI routes: usage is observed and recorded, not capped.
        # One CLI process may still make more than one physical provider request, so this is
        # an observation record, not a cost ceiling.
        output_control="OBSERVATION_ONLY",
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
        # The same output contract the Codex, xAI and Claude Messages routes send.
        schema = constrain_span_references(
            strict_output_schema(request.output_model),
            tuple(span.span_id for span in request.context_pack.evidence),
            request.context_pack.research_context,
        )
        allowed = request.context_pack.policy_hints.get("minimum_action_tier_by_family")
        if isinstance(allowed, dict):
            schema = constrain_action_families(
                schema, tuple(str(key) for key in cast(dict[object, object], allowed))
            )
        schema = apply_hypothesis_review_contract(
            schema, request.output_model, request.context_pack.research_context
        )
        schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        if len(schema_json) > _MAX_SCHEMA_CHARS:
            raise ClaudeCodeUnavailable("CLAUDE_CODE_SCHEMA_TOO_LARGE")
        prompt = prompt_envelope(request)
        payload = canonical_payload(
            {
                "provider": "claude-code",
                "prompt": prompt,
                "schema": schema,
                "model": self._model,
                "effort": effort,
                "prompt_version": request.prompt_version,
                "model_policy_ref": request.model_policy_ref,
                "max_output_tokens": request.max_output_tokens,
                "model_settings": selected,
            }
        )
        wire = payload
        dispatch_id = reserve_model_dispatch(
            wire, request.max_output_tokens, self.control_capability
        )
        argv = claude_print_argv(self._executable, self._model, effort, schema_json)
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
            process = await self._process_factory(argv, env, Path(temporary))
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
            input_tokens, output_tokens = _reported_usage(stdout)
            work.boundary.record_usage(
                dispatch_id, len(stdout), input_tokens, output_tokens, "UNKNOWN", None
            )
        if _login_required(stdout):
            # Whatever the exit code, the CLI said it is not signed in: drop the cached status.
            invalidate_claude_code_status(self._workspace)
            raise ClaudeCodeUnavailable("CLAUDE_CODE_LOGIN_REQUIRED")
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


def _login_required(stdout: bytes) -> bool:
    """The CLI's own JSON result says the profile is not signed in (`Not logged in · /login`)."""

    try:
        raw: object = json.loads(stdout)
    except ValueError:
        return False
    if not isinstance(raw, dict):
        return False
    envelope = cast(dict[str, object], raw)
    text = envelope.get("result")
    return (
        envelope.get("type") == "result"
        and envelope.get("is_error") is True
        and isinstance(text, str)
        and ("not logged in" in text.lower() or "/login" in text)
    )


def claude_code_model_factory(
    workspace: Path,
    model: str | None,
    *,
    auth_mode: ClaudeAuthMode = "PROFILE",
    explicit_environment: Mapping[str, str] | None = None,
) -> ModelPort:
    if auth_mode != "PROFILE" or explicit_environment is not None:
        raise ClaudeCodeUnavailable("CLAUDE_CODE_AUTH_MODE_UNSUPPORTED")
    if cached_claude_code_status(workspace).get("execution_eligible") is not True:
        raise ClaudeCodeUnavailable("CLAUDE_CODE_EXECUTION_UNVERIFIED")
    return ClaudeCodeModel(workspace, model=model)
