"""Command to preview and delete shared objects that nothing in a workspace refers to."""

from pathlib import Path
from typing import Annotated

import typer

from thoth.adapters.storage.object_prune import (
    MIN_AGE_SECONDS,
    ObjectPruneRefused,
    delete_confirmed_objects,
    preview_unreferenced_objects,
)
from thoth.adapters.storage.workspace_owner import WorkspaceInUse
from thoth.apps.workspace_guard import WORKSPACE_IN_USE_EXIT_CODE
from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode


def register_object_prune_commands(app: typer.Typer, default_workspace: Path) -> None:
    objects = typer.Typer(help="Maintain the objects shared by every project in a workspace.")
    app.add_typer(objects, name="objects")

    def objects_group() -> None:
        """Shared object maintenance."""

    objects.callback()(objects_group)

    def refuse(message: str, code: int = 1) -> typer.Exit:
        typer.echo(message, err=True)
        return typer.Exit(code=code)

    def prune(
        workspace: Annotated[Path, typer.Option("--workspace")] = default_workspace,
        confirm: Annotated[
            str | None,
            typer.Option("--confirm", help="SHA-256 of a preview list; deletes its objects"),
        ] = None,
    ) -> None:
        """Preview unreferenced objects, or delete the ones from a preview with --confirm."""
        if parse_deployment_mode() is not DeploymentMode.LOCAL:
            raise refuse("Object pruning is available only for a local workspace.")
        try:
            if confirm is None:
                _preview(workspace)
            else:
                _delete(workspace, confirm)
        except WorkspaceInUse as error:
            raise refuse(error.message_en, WORKSPACE_IN_USE_EXIT_CODE) from error
        except ObjectPruneRefused as error:
            raise refuse(f"Refused: {error}. Nothing was deleted.") from error

    objects.command("prune")(prune)


def _fail_on(problems: tuple[str, ...], headline: str) -> None:
    if not problems:
        return
    typer.echo(headline, err=True)
    for problem in problems:
        typer.echo(f"  - {problem}", err=True)
    raise typer.Exit(code=1)


def _preview(workspace: Path) -> None:
    report = preview_unreferenced_objects(workspace)
    _fail_on(
        report.problems,
        "Preview incomplete: the workspace could not be read completely. "
        "Nothing was deleted and no list was written.",
    )
    hours = MIN_AGE_SECONDS // 3600
    if report.list_sha256 is None or report.list_path is None:
        typer.echo(f"No unreferenced objects older than {hours} hours. Nothing to delete.")
        return
    typer.echo(
        f"Unreferenced objects older than {hours} hours: {len(report.candidates)} "
        f"({report.total_bytes} bytes)"
    )
    typer.echo(f"Candidate list: {report.list_path}")
    typer.echo(f"List SHA-256: {report.list_sha256}")
    typer.echo("Preview only; nothing was deleted. To delete these objects, run:")
    typer.echo(f'  thoth objects prune --workspace "{workspace}" --confirm {report.list_sha256}')


def _delete(workspace: Path, confirm: str) -> None:
    report = delete_confirmed_objects(workspace, confirm)
    _fail_on(
        report.problems,
        "Delete stopped: the workspace could not be read completely or a file was not removed.",
    )
    typer.echo(
        f"Deleted {report.deleted_count} objects ({report.deleted_bytes} bytes). "
        f"Skipped {report.skipped_count} that were no longer candidates."
    )
