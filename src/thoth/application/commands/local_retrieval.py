"""LOCAL-only, generation-bound retrieval preparation commands and read projections."""

from typing import Literal

from pydantic import Field, JsonValue, ValidationError, field_validator

from thoth.application.services.local_retrieval import LocalRetrievalService
from thoth.domain.base import DomainModel
from thoth.domain.embedding_index import EmbeddingIndexError
from thoth.domain.ids import Sha256
from thoth.domain.model_asset import ModelAssetError
from thoth.protocol.deferred import current_operation
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ProjectInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)


class ConfigureInput(ProjectInput):
    enabled: bool = Field(strict=True)
    expected_generation: int = Field(ge=0, strict=True)


class PrepareInput(ProjectInput):
    expected_generation: int = Field(ge=0, strict=True)


class RetryInput(ProjectInput):
    job_id: Sha256
    generation: int = Field(ge=1, strict=True)


class AssetInput(DomainModel):
    project_id: Literal["system:workspace"] = "system:workspace"


class ImportInput(AssetInput):
    source_path: str = Field(min_length=1, max_length=32768)
    manifest_digest: Sha256


class InstallInput(AssetInput):
    manifest_digest: Sha256
    confirm_download: Literal[True]

    @field_validator("confirm_download", mode="before")
    @classmethod
    def explicit_consent(cls, value: object) -> object:
        if value is not True:
            raise ValueError("MODEL_ASSET_CONSENT_REQUIRED")
        return value


def _error(exc: Exception) -> RpcApplicationError:
    reason = "EMBEDDING_TIMEOUT" if isinstance(exc, TimeoutError) else str(exc)
    if (
        not reason
        or len(reason) > 100
        or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789" for c in reason)
    ):
        reason = "LOCAL_RETRIEVAL_FAILED"
    denied = reason in {"LOCAL_RETRIEVAL_LOCAL_ONLY", "LOCAL_RETRIEVAL_SCOPE_DENIED"}
    return RpcApplicationError(
        RpcErrorCode.AUTHORIZATION_DENIED if denied else RpcErrorCode.DOMAIN_REJECTED,
        "Local retrieval request could not finish",
        data={"reason_code": reason},
    )


def _operation_id() -> str:
    operation = current_operation.get()
    if operation is None:
        raise ModelAssetError("RPC_OPERATION_REQUIRED")
    return operation.operation_id


class LocalRetrievalHandlers:
    def __init__(self, service: LocalRetrievalService) -> None:
        self.service = service

    def authorize_before_claim(self, method: str, value: dict[str, JsonValue]) -> None:
        system = method.startswith("model/localAsset/")
        project = str(value.get("project_id", "system:workspace" if system else ""))
        try:
            self.service.authorize(project, write=not method.endswith("/read"), system=system)
            if method == "model/localAsset/install":
                InstallInput.model_validate(value)
        except ModelAssetError as exc:
            raise _error(exc) from exc
        except ValidationError as exc:
            raise RpcApplicationError(
                RpcErrorCode.INVALID_PARAMS, "MODEL_ASSET_CONSENT_REQUIRED"
            ) from exc

    async def read_asset(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("model/localAsset/read", value)
        AssetInput.model_validate(value)
        try:
            return await self.service.read_asset()
        except TimeoutError as exc:
            raise _error(ModelAssetError("MODEL_ASSET_TIMEOUT")) from exc
        except OSError as exc:
            raise _error(ModelAssetError("MODEL_ASSET_IO_FAILED")) from exc
        except (ModelAssetError, EmbeddingIndexError) as exc:
            raise _error(exc) from exc

    async def read_project(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("project/retrieval/experiment/read", value)
        request = ProjectInput.model_validate(value)
        return self.service.read_project(request.project_id)

    async def configure(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("project/retrieval/experiment/configure", value)
        request = ConfigureInput.model_validate(value)
        try:
            return self.service.configure(
                request.project_id, request.enabled, request.expected_generation
            )
        except (ModelAssetError, EmbeddingIndexError) as exc:
            raise _error(exc) from exc

    async def import_asset(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("model/localAsset/import", value)
        request = ImportInput.model_validate(value)
        try:
            return await self.service.asset_action(
                operation_id=_operation_id(),
                manifest_digest=request.manifest_digest,
                source=request.source_path,
            )
        except TimeoutError as exc:
            raise _error(ModelAssetError("MODEL_ASSET_TIMEOUT")) from exc
        except OSError as exc:
            raise _error(ModelAssetError("MODEL_ASSET_IO_FAILED")) from exc
        except (ModelAssetError, EmbeddingIndexError) as exc:
            raise _error(exc) from exc

    async def install_asset(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("model/localAsset/install", value)
        request = InstallInput.model_validate(value)
        try:
            return await self.service.asset_action(
                operation_id=_operation_id(), manifest_digest=request.manifest_digest, download=True
            )
        except TimeoutError as exc:
            raise _error(ModelAssetError("MODEL_ASSET_TIMEOUT")) from exc
        except OSError as exc:
            raise _error(ModelAssetError("MODEL_ASSET_IO_FAILED")) from exc
        except (ModelAssetError, EmbeddingIndexError) as exc:
            raise _error(exc) from exc

    async def prepare(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("project/retrieval/experiment/prepare", value)
        request = PrepareInput.model_validate(value)
        try:
            return await self.service.prepare(
                request.project_id, _operation_id(), generation=request.expected_generation
            )
        except (ModelAssetError, EmbeddingIndexError, TimeoutError) as exc:
            raise _error(exc) from exc

    async def retry(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.authorize_before_claim("project/embeddingIndex/retry", value)
        request = RetryInput.model_validate(value)
        try:
            return await self.service.prepare(
                request.project_id,
                _operation_id(),
                job_id=request.job_id,
                job_generation=request.generation,
            )
        except (ModelAssetError, EmbeddingIndexError, TimeoutError) as exc:
            raise _error(exc) from exc
