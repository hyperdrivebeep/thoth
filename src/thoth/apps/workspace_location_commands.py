"""Explicit location confirmation changes only the location confirmation file."""

from pathlib import Path
from typing import Annotated

import typer

from thoth.adapters.storage import workspace_location as location_policy
from thoth.adapters.storage.workspace_location import (
    check_workspace_location,
    confirm_workspace_location,
    forget_workspace_location,
)
from thoth.apps.workspace_guard import workspace_guard
from thoth.apps.workspace_location_presentation import location_detail, location_messages
from thoth.domain.workspace_location import WorkspaceLocationError


def workspace_location_info(workspace: Path) -> dict[str, object]:
    try:
        location = check_workspace_location(workspace)
    except WorkspaceLocationError as error:
        location = error.location
    return dict(location_detail(location))


def register_workspace_location_commands(app: typer.Typer, default_workspace: Path) -> None:
    def accept_location(
        workspace: Annotated[Path, typer.Option("--workspace")] = default_workspace,
        forget: Annotated[
            bool, typer.Option("--forget", help="Remove only the saved location confirmation.")
        ] = False,
    ) -> None:
        """Confirm an unverified workspace location after reviewing the warning."""
        with workspace_guard():
            if forget:
                forget_workspace_location(workspace)
            else:
                before = location_policy.inspect_workspace_location(workspace)
                if before.kind == "UNKNOWN":
                    ko, en = location_messages(before)
                    typer.echo(ko, err=True)
                    typer.echo(en, err=True)
                location = confirm_workspace_location(workspace, expected=before)
                typer.echo(f"{location.kind}: confirmed={str(location.confirmed).lower()}")

    app.command("accept-location")(accept_location)
