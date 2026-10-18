"""Command-line guard for the one-process-per-workspace rule."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import typer

from thoth.adapters.storage.workspace_owner import WorkspaceInUse, acquire_workspace_owner

WORKSPACE_IN_USE_EXIT_CODE = 3


@contextmanager
def workspace_guard() -> Generator[None]:
    """A workspace another THOTH has open is refused with one Korean and one English line."""
    try:
        yield
    except WorkspaceInUse as error:
        typer.echo(error.message_ko, err=True)
        typer.echo(error.message_en, err=True)
        raise typer.Exit(code=WORKSPACE_IN_USE_EXIT_CODE) from error


@contextmanager
def owned_workspace(workspace: Path, *, command: str) -> Generator[None]:
    """Own the workspace for a long-running command and let go when it ends.

    A server opens its runtime on the first request, which is too late to refuse a second one.
    """
    with workspace_guard():
        ownership = acquire_workspace_owner(workspace, command=command)
    try:
        yield
    finally:
        ownership.release()
