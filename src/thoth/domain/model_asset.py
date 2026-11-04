"""Typed local asset identity, bounded preparation and operational status."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.ids import Sha256
from thoth.domain.retrieval_embedding import DenseModelPin


class AssetFile(DomainModel):
    path: str = Field(min_length=1, max_length=200)
    bytes: int = Field(gt=0, le=200_000_000)
    sha256: Sha256

    @model_validator(mode="after")
    def relative_file(self) -> "AssetFile":
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or "\\" in self.path
            or ":" in self.path
            or any(p in ("", ".", "..") for p in self.path.split("/"))
            or str(path) != self.path
        ):
            raise ValueError("MODEL_ASSET_PATH_INVALID")
        return self


class ModelAssetManifest(DomainModel):
    version: Literal["model-asset:1.0.0"] = "model-asset:1.0.0"
    model_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    preprocessing: str
    files: tuple[AssetFile, ...] = Field(min_length=1, max_length=32)
    license: str
    source: str
    max_tokens: int = Field(ge=1, le=32768)
    dimensions: Literal[384] = 384

    @model_validator(mode="after")
    def unique_paths(self) -> "ModelAssetManifest":
        paths = [f.path for f in self.files]
        if len(paths) != len(set(p.casefold() for p in paths)):
            raise ValueError("MODEL_ASSET_DUPLICATE_PATH")
        return self

    @property
    def digest(self) -> str:
        return domain_digest("MODEL_ASSET_MANIFEST", "1.0.0", canonical_payload(self))

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)


class EmbeddingLoadBudget(DomainModel):
    version: Literal["embedding-load:1.0.0"] = "embedding-load:1.0.0"
    timeout_ms: int = Field(default=10000, ge=1, le=10000)
    blas_threads: Literal[1] = 1
    tokenizer_parallelism: Literal[False] = False


class AssetImportBudget(DomainModel):
    version: Literal["asset-import:1.0.0"] = "asset-import:1.0.0"
    timeout_ms: int = Field(default=600000, ge=1, le=600000)
    lock_timeout_ms: int = Field(default=10000, ge=1, le=10000)
    cleanup_timeout_ms: int = Field(default=3000, ge=1, le=3000)


class EmbeddingAssetDescriptor(DomainModel):
    version: Literal["embedding-asset:1.0.0"] = "embedding-asset:1.0.0"
    root: str = Field(min_length=1, max_length=32768)
    manifest_digest: Sha256
    pin: DenseModelPin = DenseModelPin()
    load_budget: EmbeddingLoadBudget = EmbeddingLoadBudget()


class ModelAssetStatus(DomainModel):
    state: Literal["MODEL_REQUIRED", "READY", "FAILED"]
    manifest_digest: Sha256
    total_bytes: int
    reason_code: str | None = None


class ModelAssetError(ValueError):
    """Only an allowlisted code crosses the worker/UI boundary."""


class ModelAssetDownloadGrant(DomainModel):
    purpose: Literal["LOCAL_MODEL_ASSET_INSTALL"] = "LOCAL_MODEL_ASSET_INSTALL"
    manifest_digest: Sha256
    operation_id: str = Field(min_length=1, max_length=160)
    actor_ref: str = Field(min_length=1, max_length=160)
    approved: Literal[True]


EMBEDDING_ERROR_CODES = frozenset(
    {
        "MODEL_NOT_INSTALLED",
        "MODEL_HASH_MISMATCH",
        "EMBEDDING_DEPENDENCY_UNAVAILABLE",
        "QUERY_TOO_LONG",
        "PASSAGE_TOO_LONG",
        "EMBEDDING_PROTOCOL_FAILED",
        "EMBEDDING_TIMEOUT",
        "EMBEDDING_FAILED",
        "EMBEDDING_LOAD_FAILED",
        "EMBEDDING_NOT_PREPARED",
        "EMBEDDING_INPUT_TOO_LARGE",
    }
)
