"""Register the database-free workspace inspection and local preparation commands."""

from pathlib import Path

import typer
from rich.console import Console

from thoth.adapters.cli.diagnostics import register_diagnostic_commands
from thoth.apps.local_asset_commands import register_local_asset_commands
from thoth.apps.workspace_location_commands import register_workspace_location_commands


def register_workspace_commands(app: typer.Typer, console: Console, workspace: Path) -> None:
    register_diagnostic_commands(app, console, workspace)
    register_workspace_location_commands(app, workspace)
    register_local_asset_commands(app, workspace)
