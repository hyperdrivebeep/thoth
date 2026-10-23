"""Command-line guard for the one-process-per-workspace rule."""

from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import typer

from thoth.adapters.storage.workspace_location import (
    check_workspace_location,
    confirm_workspace_location,
)
from thoth.adapters.storage.workspace_owner import WorkspaceInUse, acquire_workspace_owner
from thoth.apps.workspace_location_presentation import location_messages
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.storage_context import storage_deployment_mode
from thoth.domain.workspace_location import WorkspaceLocation, WorkspaceLocationError

WORKSPACE_IN_USE_EXIT_CODE = 3


@contextmanager
def workspace_guard(workspace: Path | None = None) -> Generator[None]:
    """A workspace another THOTH has open is refused with one Korean and one English line."""
    try:
        if workspace is not None and storage_deployment_mode() is DeploymentMode.LOCAL:
            _prepare_cli_location(workspace)
        yield
    except WorkspaceLocationError as error:
        ko, en = location_messages(error.location)
        typer.echo(ko, err=True)
        typer.echo(en, err=True)
        raise typer.Exit(code=4 if error.location.blocked else 5) from error
    except WorkspaceInUse as error:
        typer.echo(error.message_ko, err=True)
        typer.echo(error.message_en, err=True)
        raise typer.Exit(code=WORKSPACE_IN_USE_EXIT_CODE) from error


def _interactive_confirmation() -> bool:
    return sys.stdin.isatty()


def _warn_location(location: WorkspaceLocation) -> None:
    if location.kind == "REMOVABLE" or (location.kind == "UNKNOWN" and location.confirmed):
        ko, en = location_messages(location)
        typer.echo(f"{ko} / {en}", err=True)


def _prepare_cli_location(workspace: Path) -> None:
    try:
        location = check_workspace_location(workspace)
    except WorkspaceLocationError as error:
        if error.location.blocked or not _interactive_confirmation():
            raise
        ko, en = location_messages(error.location)
        if not typer.confirm(f"{ko}\n{en}", default=False, err=True):
            raise typer.Exit(code=5) from error
        location = confirm_workspace_location(workspace, expected=error.location)
    _warn_location(location)


@contextmanager
def owned_workspace(workspace: Path, *, command: str) -> Generator[None]:
    """Own the workspace for a long-running command and let go when it ends.

    A server opens its runtime on the first request, which is too late to refuse a second one.
    """
    with workspace_guard():
        if storage_deployment_mode() is DeploymentMode.LOCAL:
            _warn_location(check_workspace_location(workspace))
        ownership = acquire_workspace_owner(workspace, command=command)
    try:
        yield
    finally:
        ownership.release()
