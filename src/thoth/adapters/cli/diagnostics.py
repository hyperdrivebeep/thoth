"""Local diagnostics registered on the caller's existing CLI app."""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from thoth.adapters.environment import load_environment_profile


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def register_diagnostic_commands(
    app: typer.Typer, console: Console, default_workspace: Path
) -> None:
    DEFAULT_WORKSPACE = default_workspace

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

    app.command()(doctor)

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

    app.command("connector-sandbox-doctor")(connector_sandbox_doctor)

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

    app.command("profile-check")(profile_check)
