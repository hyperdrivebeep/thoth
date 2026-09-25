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
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, cast

import orjson
import typer
from rich.console import Console
from rich.table import Table

from thoth import __version__
from thoth.adapters.connectors import load_connector_registry
from thoth.adapters.environment import load_environment_profile
from thoth.adapters.models import (
    CodexCliExecutor,
    CodexOAuthModel,
    codex_oauth_status,
    run_codex_device_login,
)
from thoth.adapters.projectpacks import load_project_pack
from thoth.adapters.runtime import SystemClock
from thoth.adapters.sandbox import default_sandbox_factory_registry
from thoth.adapters.storage import SqliteConversationSessionStore
from thoth.application.services.conversation_router import ConversationRouter
from thoth.application.services.tui_session_service import TuiSessionService
from thoth.apps.conversation_dispatch import BusConversationDispatcher
from thoth.apps.projectpack_execution import run_project_pack
from thoth.apps.runtime import create_runtime
from thoth.ports.sandbox import SandboxPort
from thoth.protocol.bus import CommandBus
from thoth.protocol.stdio import handle_json_line

app = typer.Typer(no_args_is_help=False, add_completion=False)
console = Console()


def workspace_session_id(workspace: Path) -> str:
    normalized = os.path.normcase(str(workspace.resolve()))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"tui:workspace:{digest}:local"


@app.callback(invoke_without_command=True)
def main(
    context: typer.Context,
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth"),
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
    presenter = asyncio.create_task(_present_research_progress(tui, runtime.bus))
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
            _render_tui_turn(await tui.execute(line))
    finally:
        presenter.cancel()
        await asyncio.gather(presenter, return_exceptions=True)
        runtime.bus.close_tasks()
        await runtime.bus.drain()
        runtime.close()


async def _present_research_progress(tui: TuiSessionService, bus: CommandBus) -> None:
    from thoth.protocol.jsonrpc import JsonRpcRequest

    previous = None
    cursor = 0
    while True:
        await asyncio.sleep(1)
        session = tui.current()
        if session.active_project_id is None or session.active_thread_id is None:
            continue
        cursor += 1
        response = await bus.query(
            JsonRpcRequest.model_validate(
                {
                    "id": f"tui-progress:{cursor}",
                    "method": "thread/read",
                    "params": {
                        "_meta": {
                            "idempotencyKey": f"tui-progress:{session.session_id}:"
                            f"{id(tui)}:{cursor}"
                        },
                        "input": {
                            "project_id": session.active_project_id,
                            "thread_id": session.active_thread_id,
                        },
                    },
                }
            )
        )
        if response.result is None:
            continue
        value = response.result.get("value")
        if not isinstance(value, dict):
            continue
        from thoth.application.services.research_progress_view import progress_view

        status = progress_view(value)
        if status != previous:
            previous = status
            console.print_json(json.dumps(status))
            console.print(str(status["usage_summary"]), markup=False)
            reason = status.get("terminal_reason")
            rejection = status.get("http_rejection")
            if isinstance(reason, str) and reason:
                detail = "미확인"
                if isinstance(rejection, dict):
                    rejection_kind = cast(dict[str, object], rejection).get("rejection_kind")
                    if rejection_kind:
                        detail = str(rejection_kind)
                console.print(
                    f"연구 상태: 보류 — {reason} / 원인 상세: {detail}",
                    markup=False,
                )


def _render_tui_turn(turn: object) -> None:
    from thoth.domain.conversation import TuiTurnResult

    value = TuiTurnResult.model_validate(turn)
    payload: dict[str, object] = {
        "intent": value.candidate.intent.value,
        "status": value.status.value,
        "project_id": value.session.active_project_id,
        "thread_id": value.session.active_thread_id,
        "message": value.candidate.display_message,
    }
    if value.error_message is not None:
        payload["hold"] = {
            "code": value.error_code,
            "message": value.error_message,
        }
    if value.response:
        payload["result"] = _tui_result_summary(value.response)
    console.print_json(json.dumps(payload))


def _tui_result_summary(value: Mapping[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for key in (
        "project_id",
        "thread_id",
        "lifecycle",
        "execution_state",
        "current_object_ids",
        "working_head_digest",
        "status",
        "contract_version",
        "operation_id",
        "request_epoch",
        "request_ref",
        "input_id",
        "input_state",
        "phase",
        "freshness",
        "current_result",
        "previous_result",
        "attempt",
        "budget",
        "settings_digest",
        "selection",
        "effective_settings",
        "model_options",
        "model_settings",
        "answer_outcome",
        "resume_information",
        "completed_stages",
        "usage",
        "failure",
        "execution_summary",
        "model_dispatches",
    ):
        if key in value:
            summary[key] = value[key]
    for key in (
        "criterion_profile_decision",
        "autonomous_acquisition",
        "critical_counter_search",
        "r2_closed_loop",
        "recursive_improvement",
    ):
        child = value.get(key)
        if isinstance(child, dict):
            child_values = cast(dict[str, object], child)
            summary[key] = {
                name: child_values[name]
                for name in ("state", "terminal_state", "hold_reason", "receipt_digest")
                if name in child_values
            }
    portfolio = value.get("portfolio")
    if isinstance(portfolio, dict):
        portfolio_values = cast(dict[str, object], portfolio)
        hypotheses = portfolio_values.get("hypotheses")
        summary["hypothesis_count"] = (
            len(cast(list[object], hypotheses)) if isinstance(hypotheses, list) else 0
        )
    action_plan = value.get("action_plan")
    if isinstance(action_plan, dict):
        action_values = cast(dict[str, object], action_plan)
        alternatives = action_values.get("alternatives")
        summary["action_count"] = (
            len(cast(list[object], alternatives)) if isinstance(alternatives, list) else 0
        )
    for key in ("assessment", "commit", "export", "exports", "activities"):
        if key in value:
            summary[key] = value[key]
    return summary or {"acknowledged": True}


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


@app.command()
def doctor(
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth"),
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
def auth_status(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Report Codex OAuth connectivity without reading or printing tokens."""
    payload = codex_oauth_status()
    if json_output:
        console.print_json(json.dumps(payload))
    else:
        state = "CONNECTED" if payload["connected"] else "LOGIN REQUIRED"
        console.print(f"Codex OAuth: {state}")
    if not payload["connected"]:
        raise typer.Exit(code=1)


@app.command("auth-connect")
def auth_connect() -> None:
    """Delegate ChatGPT device OAuth to the official Codex CLI."""
    status = codex_oauth_status()
    if status["connected"]:
        console.print("Codex OAuth is already connected.")
        return
    exit_code = run_codex_device_login()
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@app.command("model-probe")
def model_probe(
    model: Annotated[str | None, typer.Option("--model")] = None,
) -> None:
    """Run one synthetic structured-output canary through Codex OAuth."""
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["OK"]},
            "message": {"type": "string"},
        },
        "required": ["status", "message"],
        "additionalProperties": False,
    }
    prompt = (
        "Return one JSON object matching the supplied schema. "
        "Set status to OK and message to THOTH OAuth model connection verified. "
        "Do not use tools or inspect files."
    )
    raw = asyncio.run(CodexCliExecutor(model=model).execute(prompt, schema))
    payload_value = cast(object, json.loads(raw))
    if not isinstance(payload_value, dict):
        raise typer.Exit(code=1)
    payload = cast(dict[str, object], payload_value)
    if payload.get("status") != "OK":
        raise typer.Exit(code=1)
    console.print_json(json.dumps(payload))


@app.command("source-stage")
def source_stage(
    source: Annotated[Path, typer.Option("--source", exists=True, dir_okay=False)],
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth"),
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
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth"),
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
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth"),
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
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth"),
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
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth"),
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
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth-pack"),
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
    loaded = load_project_pack(pack, include_scripted=selected_provider == "scripted")
    selected_model = (
        None
        if selected_provider == "scripted"
        else CodexOAuthModel(CodexCliExecutor(model=model_name))
    )
    result = asyncio.run(run_project_pack(loaded, workspace=workspace, model=selected_model))
    sys.stdout.buffer.write(
        orjson.dumps(result.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS) + b"\n"
    )


@app.command()
def serve(
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth"),
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    """Run the local HTTP app server on loopback by default."""
    import uvicorn

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise typer.BadParameter("V0 local server only permits loopback hosts")
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
    workspace: Annotated[Path, typer.Option("--workspace")] = Path(".thoth"),
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
    os.environ["THOTH_WORKSPACE"] = str(workspace.resolve())
    if web_ui_dist() is None:
        console.print(
            "apps/web/dist 없음. `npm --prefix apps/web run build` 후 다시 실행."
        )
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
        program_files_x86=Path(
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        ),
    )
    if command is not None:
        subprocess.Popen(command)
    else:
        webbrowser.open(url)
    thread.join()


if __name__ == "__main__":
    app()
