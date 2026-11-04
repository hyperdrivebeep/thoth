from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from thoth.domain.model_asset import (
    EmbeddingAssetDescriptor,
    ModelAssetDownloadGrant,
    ModelAssetManifest,
    ModelAssetStatus,
)


class ModelAssetStorePort(Protocol):
    manifest: ModelAssetManifest

    def status(self, *, deadline: float | None = None) -> ModelAssetStatus: ...
    def descriptor(self, *, deadline: float | None = None) -> EmbeddingAssetDescriptor: ...
    async def import_directory(
        self,
        source: Path,
        *,
        deadline: float | None = None,
        progress: Callable[[int], None] | None = None,
        check_authority: Callable[[], None] | None = None,
    ) -> ModelAssetStatus: ...


class ModelAssetDownloadPort(Protocol):
    async def install(
        self,
        grant: ModelAssetDownloadGrant,
        *,
        deadline: float,
        progress: Callable[[str, int], None],
        check_authority: Callable[[], None],
    ) -> ModelAssetStatus: ...
