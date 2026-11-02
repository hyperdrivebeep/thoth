"""Explicit offline model-file preparation, without a database or encoder process."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from thoth.adapters.embeddings.assets import LocalModelAssets
from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode
from thoth.domain.model_asset import ModelAssetError


def register_local_asset_commands(app: typer.Typer, default_workspace: Path) -> None:
    model = typer.Typer(help="Prepare fixed local retrieval files; never activate search.")
    app.add_typer(model, name="local-model")

    def store(workspace: Path) -> LocalModelAssets:
        if parse_deployment_mode() is not DeploymentMode.LOCAL:
            raise typer.BadParameter("LOCAL_RETRIEVAL_LOCAL_ONLY")
        return LocalModelAssets(workspace / "models")

    def status(workspace: Annotated[Path, typer.Option("--workspace")] = default_workspace) -> None:
        typer.echo(store(workspace).status().model_dump_json())

    def import_files(
        source: Annotated[
            Path, typer.Argument(help="Existing local folder with fixed manifest files")
        ],
        workspace: Annotated[Path, typer.Option("--workspace")] = default_workspace,
    ) -> None:
        try:
            result = asyncio.run(store(workspace).import_directory(source))
        except (ModelAssetError, TimeoutError) as exc:
            typer.echo(str(exc) or "MODEL_ASSET_TIMEOUT", err=True)
            raise typer.Exit(1) from exc
        typer.echo(result.model_dump_json())

    model.command("status")(status)
    model.command("import")(import_files)
