from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import orjson
import typer
from rich.console import Console
from rich.table import Table

from thoth import __version__
from thoth.adapters.connectors import load_connector_registry
from thoth.adapters.environment import load_environment_profile
from thoth.adapters.models import (
    codex_oauth_status,
)
from thoth.adapters.models.claude_code import ClaudeCodeUnavailable, run_claude_code_login
from thoth.adapters.models.codex_broker import close_workspace_broker
from thoth.adapters.models.local_credentials import start_codex_login
from thoth.adapters.projectpacks import load_project_pack
from thoth.adapters.runtime import SystemClock
from thoth.adapters.sandbox import default_sandbox_factory_registry
from thoth.adapters.storage import SqliteConversationSessionStore
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.apps.model_composition import create_codex_model
from thoth.apps.projectpack_execution import run_project_pack
from thoth.apps.runtime import create_runtime
from thoth.apps.tui_presentation import present_research_progress, render_tui_turn
from thoth.apps.workspace_paths import (
    default_workspace,
    legacy_workspace_candidates,
    selected_workspace,
    workspace_id,
)
from thoth.domain.base import DomainModel
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest
from thoth.ports.model import ModelExecutionHold
from thoth.ports.sandbox import SandboxPort
from thoth.protocol.stdio import handle_json_line

app = typer.Typer(no_args_is_help=False, add_completion=False)
console = Console()
DEFAULT_WORKSPACE = default_workspace()


class ModelProbeOutput(DomainModel):
    status: Literal["OK"]
    message: str


def workspace_session_id(workspace: Path) -> str:
    normalized = os.path.normcase(str(workspace.resolve()))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"tui:workspace:{digest}:local"


@app.callback(invoke_without_command=True)
def main(
    context: typer.Context,
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    sandbox_profile: Annotated[
        str,
        typer.Option(
            "--sandbox-profile",
            help="disabled, docker-poc, gvisor, or managed-e2b",
        ),
    ] = "disabled",
    connector_config: Annotated[
        Path | None,
        typer.Option("--connector-config", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """Open the CLI shell when no subcommand is supplied."""
    if context.invoked_subcommand is not None:
        return
    announce_workspace(workspace, command="thoth")
    _interactive_shell(
        workspace,
        sandbox_profile=sandbox_profile,
        connector_config=connector_config,
    )


def _interactive_shell(
    workspace: Path,
    *,
    sandbox_profile: str = "disabled",
    connector_config: Path | None = None,
) -> None:
    asyncio.run(
        _interactive_session(
            workspace, sandbox_profile=sandbox_profile, connector_config=connector_config
        )
    )


def announce_workspace(workspace: Path, *, command: str) -> None:
    root = workspace.resolve()
    typer.echo(f"THOTH workspace: {root} ({workspace_id(root)})", err=True)
    for legacy in legacy_workspace_candidates(Path.cwd()):
        if legacy != root:
            typer.echo(
                f"Existing local workspace found: {legacy}. "
                f'To resume it explicitly: {command} --workspace "{legacy}". '
                "No data was moved.",
                err=True,
            )


async def _interactive_session(
    workspace: Path,
    *,
    sandbox_profile: str = "disabled",
    connector_config: Path | None = None,
) -> None:
    runtime = create_runtime(
        workspace,
        sandbox_adapter=_sandbox_adapter(sandbox_profile, workspace),
        connector_registry=(
            None if connector_config is None else load_connector_registry(connector_config)
        ),
    )
    tui = TuiSessionService(
        session_id=workspace_session_id(workspace),
        store=SqliteConversationSessionStore(runtime.ledger.engine),
        router=ConversationRouter(),
        dispatcher=BusConversationDispatcher(runtime.bus),
        clock=SystemClock(),
    )
    console.print(
        "THOTH CLI shell — natural-language mode. "
        "Use /help, or /rpc <JSON> as a developer escape hatch."
    )
    presenter = asyncio.create_task(present_research_progress(tui, runtime.bus, console))
    try:
        while True:
            try:
                line = (await asyncio.to_thread(input, "thoth> ")).strip()
            except EOFError:
                break
            if not line:
                continue
            if line in {"/quit", "/exit"}:
                break
            if line == "/help":
                console.print(
                    '/project <id> | /project create <id> "<name>" <cutoff>\n'
                    "/thread <problem> | /thread use <id>\n"
                    "/new (clear active Thread; next text starts a new one)\n"
                    "/source <relative-path> [--project-shared] [--eligible] [--informal]\n"
                    "/status | /history\n"
                    "/compare <from-digest> <to-digest>\n"
                    "/restore <aggregate> <target-digest> <current-digest>\n"
                    "/pause | /resume | /stop | /export\n"
                    "/model [provider model effort] | /reasoning <effort> [--project]\n"
                    "/retry | /usage\n"
                    "/rpc <JSON-RPC request> | /help | /quit"
                )
                continue
            if line.startswith("/rpc "):
                response = await handle_json_line(line.removeprefix("/rpc ").encode(), runtime.bus)
                sys.stdout.buffer.write(response + b"\n")
                sys.stdout.buffer.flush()
                continue
            render_tui_turn(await tui.execute(line), console)
    finally:
        presenter.cancel()
        await asyncio.gather(presenter, return_exceptions=True)
        runtime.bus.close_tasks()
        await runtime.bus.drain()
        runtime.close()


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _sandbox_adapter(profile: str, workspace: Path) -> SandboxPort | None:
    try:
        return default_sandbox_factory_registry().create(profile, workspace)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def version(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Report build and protocol versions."""
    payload = {
        "product": "THOTH",
        "version": __version__,
        "protocol_version": "0.1.0",
        "python": platform.python_version(),
    }
    if json_output:
        console.print_json(json.dumps(payload))
        return
    console.print(f"THOTH {payload['version']} (protocol {payload['protocol_version']})")


@app.command("workspace")
def workspace_info(
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Show the selected data root and legacy paths without opening or moving them."""
    root = selected_workspace(workspace)
    legacy = legacy_workspace_candidates(Path.cwd())
    console.print_json(
        json.dumps(
            {
                "workspace": str(root),
                "workspace_id": workspace_id(root),
                "selection": "EXPLICIT" if workspace is not None else "STABLE_DEFAULT",
                "legacy_candidates": [str(path) for path in legacy if path != root],
                "resume_hint": (
                    "Pass --workspace <existing-path> to serve or desktop; no data is moved."
                ),
            }
        )
    )


@app.command("claude-code-login")
def claude_code_login(
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    console_login: Annotated[bool, typer.Option("--console")] = False,
) -> None:
    """Open the official Claude Code sign-in for THOTH's isolated profile."""
    try:
        code = run_claude_code_login(workspace, console=console_login)
    except ClaudeCodeUnavailable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=code)


@app.command()
def doctor(
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check local runtime capabilities without external calls."""
    checks = {
        "python_supported": platform.python_version_tuple() >= ("3", "12", "0"),
        "sqlite_fts5": False,
        "fastapi": _module_available("fastapi"),
        "pydantic": _module_available("pydantic"),
        "sqlalchemy": _module_available("sqlalchemy"),
        "pypdf": _module_available("pypdf"),
        "openpyxl": _module_available("openpyxl"),
        "workspace_parent_writable": workspace.parent.exists(),
    }
    with sqlite3.connect(":memory:") as connection:
        try:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
            checks["sqlite_fts5"] = True
        except sqlite3.OperationalError:
            checks["sqlite_fts5"] = False

    payload = {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
    if json_output:
        console.print_json(json.dumps(payload))
    else:
        table = Table(title="THOTH doctor")
        table.add_column("Capability")
        table.add_column("Status")
        for name, passed in checks.items():
            table.add_row(name, "PASS" if passed else "FAIL")
        console.print(table)
        console.print(f"Overall: {payload['status']}")
    if payload["status"] != "PASS":
        raise typer.Exit(code=1)


@app.command("connector-sandbox-doctor")
def connector_sandbox_doctor(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect optional connector SDKs and sandbox runtimes without executing code."""
    docker_cli = shutil.which("docker") is not None
    docker_daemon = False
    docker_error: str | None = None
    if docker_cli:
        try:
            completed = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            docker_daemon = completed.returncode == 0 and bool(completed.stdout.strip())
            if not docker_daemon:
                docker_error = "DAEMON_UNAVAILABLE"
        except (OSError, subprocess.TimeoutExpired):
            docker_error = "DOCKER_PROBE_FAILED"
    checks = {
        "mcp_sdk": _module_available("mcp"),
        "boto3": _module_available("boto3"),
        "psycopg": _module_available("psycopg"),
        "docker_cli": docker_cli,
        "docker_daemon": docker_daemon,
    }
    payload = {
        "status": "PASS" if all(checks.values()) else "CONDITIONAL",
        "checks": checks,
        "docker_error": docker_error,
        "execution_performed": False,
        "production_accreditation_claimed": False,
    }
    if json_output:
        console.print_json(json.dumps(payload))
        return
    table = Table(title="THOTH connector/sandbox doctor")
    table.add_column("Capability")
    table.add_column("Status")
    for name, passed in checks.items():
        table.add_row(name, "PASS" if passed else "CONDITIONAL")
    console.print(table)
    console.print(f"Overall: {payload['status']}")


@app.command("profile-check")
def profile_check(
    profile: Annotated[Path, typer.Option("--profile", exists=True, dir_okay=False)],
) -> None:
    """Validate one deployment/model/connector boundary profile without activating it."""
    value = load_environment_profile(profile)
    status = (
        "PASS"
        if value.adapter_available
        and (value.sandbox_route.value == "DISABLED" or value.sandbox_adapter_available)
        else "CONDITIONAL"
    )
    console.print_json(
        json.dumps(
            {
                "status": status,
                "profile": value.model_dump(mode="json"),
                "activation_performed": False,
                "runtime_health_probed": False,
            }
        )
    )


@app.command("auth-status")
def auth_status(
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Report isolated Codex connectivity without exposing credentials."""
    try:
        payload = codex_oauth_status(workspace)
    except ModelExecutionHold as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        close_workspace_broker(workspace)
    if json_output:
        safe = {
            key: payload.get(key)
            for key in (
                "provider",
                "connected",
                "connection_state",
                "profile_mode",
                "reason_code",
                "execution_eligible",
                "execution_verified",
            )
            if key in payload
        }
        console.print_json(json.dumps(safe))
    else:
        state = str(payload.get("connection_state") or "UNKNOWN")
        console.print(f"Codex account: {state}")
    if not payload["connected"]:
        raise typer.Exit(code=1)


@app.command("auth-connect")
def auth_connect(
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
) -> None:
    """Keep the official login listener owned until completion or bounded timeout."""
    try:
        status = codex_oauth_status(workspace)
        if status["connected"]:
            console.print("Codex account is already connected in this THOTH workspace.")
            return
        result = start_codex_login(workspace, wait_for_completion=True)
    except ModelExecutionHold as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        close_workspace_broker(workspace)
    safe = {
        key: result.get(key)
        for key in (
            "started",
            "connected",
            "connection_state",
            "reason_code",
            "execution_eligible",
            "execution_verified",
            "guidance",
        )
        if key in result
    }
    console.print_json(json.dumps(safe))
    if result.get("connected") is not True:
        raise typer.Exit(code=1)


@app.command("model-probe")
def model_probe(
    model: Annotated[str | None, typer.Option("--model")] = None,
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
) -> None:
    """Use the normal workspace-bound model-only transport for one synthetic canary."""
    prompt = (
        "Return one JSON object matching the supplied schema. "
        "Set status to OK and message to THOTH OAuth model connection verified. "
        "This is a synthetic connectivity check with no attached sources."
    )
    request = ModelRequest(
        role=ModelRole.USER_EXPLAINER,
        project_id="system:workspace",
        cutoff_at=datetime.now(UTC),
        context_pack=ContextPack(
            case_id="case:model-probe",
            project_id="system:workspace",
            object_id="object:model-probe",
            problem=prompt,
            evidence=(),
            criteria=(),
            sufficiency=None,
            input_head_set_digest="0" * 64,
        ),
        output_model=ModelProbeOutput,
        prompt_version="model-probe.v1",
        model_policy_ref="model-probe:workspace-bound-v1",
        max_output_tokens=256,
    )
    try:
        result = asyncio.run(create_codex_model(workspace, model).structured(request))
    except ModelExecutionHold as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        close_workspace_broker(workspace)
    if result.scripted or result.output.status != "OK":
        raise typer.Exit(code=1)
    console.print_json(result.output.model_dump_json())


@app.command("source-stage")
def source_stage(
    source: Annotated[Path, typer.Option("--source", exists=True, dir_okay=False)],
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
) -> None:
    """Copy one user-selected source into the bounded workspace inbox."""
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    inbox = workspace.resolve() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    destination = inbox / f"{digest[:16]}-{source.name}"
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise typer.BadParameter("existing staged path has different content")
    if not destination.exists():
        shutil.copy2(source, destination)
    console.print_json(
        json.dumps(
            {
                "relative_path": destination.relative_to(inbox).as_posix(),
                "byte_sha256": digest,
                "bytes": len(raw),
            }
        )
    )


@app.command("git-snapshot")
def git_snapshot(
    repository: Annotated[Path, typer.Option("--repository", exists=True, file_okay=False)],
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
) -> None:
    """Create a read-only Git identity/status manifest in the workspace inbox."""
    resolved = repository.resolve()

    def git(*arguments: str) -> bytes:
        completed = subprocess.run(
            ["git", "-C", str(resolved), *arguments],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise typer.BadParameter("Git metadata command failed")
        return completed.stdout

    top = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
    head = git("rev-parse", "HEAD").decode().strip()
    status_records = [
        item for item in git("status", "--porcelain=v1", "-z").decode().split("\0") if item
    ]
    status_by_path = {
        record[3:]: record[:2]
        for record in status_records
        if len(record) >= 4 and " -> " not in record[3:]
    }
    entries: list[dict[str, object]] = []
    for record in git("ls-files", "--stage", "-z").decode().split("\0"):
        if not record:
            continue
        metadata_value, path = record.split("\t", 1)
        mode, object_id, stage = metadata_value.split(" ", 2)
        entries.append(
            {
                "path": path,
                "mode": mode,
                "object_id": object_id,
                "stage": int(stage),
                "status": status_by_path.get(path),
            }
        )
    manifest: dict[str, object] = {
        "repository": top.name,
        "head": head,
        "dirty": bool(status_records),
        "entries": entries,
    }
    raw = orjson.dumps(manifest, option=orjson.OPT_SORT_KEYS)
    digest = hashlib.sha256(raw).hexdigest()
    inbox = workspace.resolve() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    destination = inbox / f"{digest[:16]}-{top.name}.git-manifest.json"
    if not destination.exists():
        destination.write_bytes(raw)
    console.print_json(
        json.dumps(
            {
                "relative_path": destination.relative_to(inbox).as_posix(),
                "byte_sha256": digest,
                "head": head,
                "dirty": bool(status_records),
                "entry_count": len(entries),
            }
        )
    )


@app.command("run")
def run_request(
    request: Annotated[Path, typer.Option("--request", exists=True, dir_okay=False)],
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    sandbox_profile: Annotated[str, typer.Option("--sandbox-profile")] = "disabled",
    connector_config: Annotated[
        Path | None,
        typer.Option("--connector-config", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """Execute one JSON-RPC request and emit one machine-readable JSON result."""
    runtime = create_runtime(
        workspace,
        sandbox_adapter=_sandbox_adapter(sandbox_profile, workspace),
        connector_registry=(
            None if connector_config is None else load_connector_registry(connector_config)
        ),
    )
    try:

        async def run_and_finish() -> bytes:
            from thoth.protocol.jsonrpc import JsonRpcError, JsonRpcResponse
            from thoth.protocol.stdio import encode_response

            admitted = await handle_json_line(request.read_bytes(), runtime.bus)
            await runtime.bus.drain()
            response = JsonRpcResponse.model_validate_json(admitted)
            identifier = None if response.result is None else response.result.get("operation_id")
            operation = (
                runtime.bus.read_operation(identifier) if isinstance(identifier, str) else None
            )
            if operation is not None:
                if operation.error is not None:
                    return encode_response(
                        JsonRpcResponse(
                            id=response.id, error=JsonRpcError.model_validate(operation.error)
                        )
                    )
                if operation.result is not None:
                    return encode_response(
                        JsonRpcResponse(
                            id=response.id,
                            result={
                                "operation_id": operation.operation_id,
                                "state": operation.state.value,
                                "value": operation.result,
                            },
                        )
                    )
            return admitted

        response = asyncio.run(run_and_finish())
    finally:
        runtime.close()
    sys.stdout.buffer.write(response + b"\n")


@app.command("rpc")
def rpc_stdio(
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    sandbox_profile: Annotated[str, typer.Option("--sandbox-profile")] = "disabled",
    connector_config: Annotated[
        Path | None,
        typer.Option("--connector-config", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """Serve JSON-RPC over stdin/stdout JSONL until EOF."""
    runtime = create_runtime(
        workspace,
        sandbox_adapter=_sandbox_adapter(sandbox_profile, workspace),
        connector_registry=(
            None if connector_config is None else load_connector_registry(connector_config)
        ),
    )
    try:

        async def serve() -> None:
            while line := await asyncio.to_thread(sys.stdin.buffer.readline):
                response = await handle_json_line(line, runtime.bus)
                sys.stdout.buffer.write(response + b"\n")
                sys.stdout.buffer.flush()
            await runtime.bus.drain()

        asyncio.run(serve())
    finally:
        runtime.close()


@app.command("events")
def events(
    project_id: Annotated[str, typer.Option("--project-id")],
    operation_id: Annotated[str, typer.Option("--operation-id")],
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
) -> None:
    """Read one operation's hash-chained events and latest checkpoint."""
    request = {
        "jsonrpc": "2.0",
        "id": f"events-{operation_id}",
        "method": "operation/read",
        "params": {
            "_meta": {"idempotencyKey": f"events-{operation_id}"},
            "input": {"project_id": project_id, "operation_id": operation_id},
        },
    }
    runtime = create_runtime(workspace)
    try:
        response = asyncio.run(handle_json_line(orjson.dumps(request), runtime.bus))
    finally:
        runtime.close()
    sys.stdout.buffer.write(response + b"\n")


@app.command("run-pack")
def run_pack(
    pack: Annotated[Path, typer.Option("--pack", exists=True, file_okay=False)],
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
    scripted: Annotated[bool, typer.Option("--scripted")] = False,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model_name: Annotated[str | None, typer.Option("--model")] = None,
) -> None:
    """Run one late-bound ProjectPack through ScriptedModel or Codex OAuth."""
    selected_provider = provider or "scripted"
    if scripted and provider not in {None, "scripted"}:
        raise typer.BadParameter("--scripted cannot be combined with another provider")
    if selected_provider not in {"scripted", "codex-oauth"}:
        raise typer.BadParameter("provider must be scripted or codex-oauth")
    if selected_provider == "codex-oauth" and workspace is None:
        raise typer.BadParameter(
            "Codex run-pack requires explicit --workspace matching auth-connect and model-probe"
        )
    pack_workspace = Path(".thoth-pack") if workspace is None else workspace.resolve()
    loaded = load_project_pack(pack, include_scripted=selected_provider == "scripted")
    try:
        selected_model = (
            None
            if selected_provider == "scripted"
            else create_codex_model(pack_workspace, model_name)
        )
        result = asyncio.run(
            run_project_pack(loaded, workspace=pack_workspace, model=selected_model)
        )
    except ModelExecutionHold as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        if selected_provider == "codex-oauth":
            close_workspace_broker(pack_workspace)
    sys.stdout.buffer.write(
        orjson.dumps(result.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS) + b"\n"
    )


@app.command()
def serve(
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Run the local HTTP app server on loopback by default."""
    import uvicorn

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise typer.BadParameter("V0 local server only permits loopback hosts")
    announce_workspace(workspace, command="thoth serve")
    os.environ["THOTH_WORKSPACE"] = str(workspace.resolve())
    from thoth.adapters.http import app as local_app

    uvicorn.run(local_app, host=host, port=port, reload=False)


@app.command("hosted-review-serve")
def hosted_review_serve(
    workspace: Annotated[Path, typer.Option("--workspace")] = Path("/var/lib/thoth/session"),
    host: Annotated[str, typer.Option("--host")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8080,
) -> None:
    """Bind the judge-facing review server. Local serve/desktop stay on loopback."""
    import uvicorn

    from thoth.adapters.http.app import create_app
    from thoth.domain.deployment_mode import DeploymentMode

    if host != "0.0.0.0":
        raise typer.BadParameter("hosted-review-serve binds 0.0.0.0 only")
    os.environ["THOTH_DEPLOYMENT_MODE"] = DeploymentMode.HOSTED_REVIEW.value
    os.environ["THOTH_WORKSPACE"] = str(workspace.resolve())
    workspace.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(), host=host, port=port, reload=False)


def desktop_window_command(
    url: str,
    *,
    system: str,
    program_files: Path,
    program_files_x86: Path,
) -> list[str] | None:
    if system != "Windows":
        return None
    for root in (program_files, program_files_x86):
        edge = root / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if edge.is_file():
            return [str(edge), f"--app={url}"]
    return None


@app.command()
def desktop(
    workspace: Annotated[Path, typer.Option("--workspace")] = DEFAULT_WORKSPACE,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Serve the existing web UI and local backend in one loopback window."""
    import threading
    import time
    import urllib.error
    import urllib.request
    import webbrowser

    import uvicorn

    from thoth.adapters.http.app import web_ui_dist

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise typer.BadParameter("V0 local server only permits loopback hosts")
    announce_workspace(workspace, command="thoth desktop")
    os.environ["THOTH_WORKSPACE"] = str(workspace.resolve())
    if web_ui_dist() is None:
        console.print("apps/web/dist 없음. `npm --prefix apps/web run build` 후 다시 실행.")
    from thoth.adapters.http import app as local_app

    url = f"http://{host}:{port}/"
    thread = threading.Thread(
        target=lambda: uvicorn.run(local_app, host=host, port=port, reload=False),
        daemon=True,
    )
    thread.start()
    health = f"http://{host}:{port}/healthz"
    for _ in range(50):
        try:
            urllib.request.urlopen(health, timeout=0.4)
            break
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    else:
        console.print("desktop API did not become ready on loopback")
        raise typer.Exit(code=1)
    command = desktop_window_command(
        url,
        system=platform.system(),
        program_files=Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
        program_files_x86=Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
    )
    if command is not None:
        subprocess.Popen(command)
    else:
        webbrowser.open(url)
    thread.join()


if __name__ == "__main__":
    app()
