from __future__ import annotations

from typing import Protocol, cast

from thoth.adapters.connectors.common import content_digest, optional_text, selector_text, utc_now
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCapability,
    ConnectorCheckpoint,
    ConnectorErrorCode,
    ConnectorFailure,
    ConnectorFetchResult,
    ConnectorOperation,
    ConnectorSelectorContract,
    NativeVersion,
    NativeVersionKind,
    SelectorFieldSpec,
)


class StreamingBodyPort(Protocol):
    def read(self, amt: int | None = None) -> bytes: ...


class S3ClientPort(Protocol):
    def head_object(self, **kwargs: str) -> dict[str, object]: ...
    def get_object(self, **kwargs: str) -> dict[str, object]: ...


class LazyBoto3S3Client:
    def __init__(self, *, endpoint_url: str | None, region_name: str | None) -> None:
        self._endpoint_url = endpoint_url
        self._region_name = region_name
        self._value: S3ClientPort | None = None

    def _client(self) -> S3ClientPort:
        if self._value is None:
            import boto3

            value = boto3.client(  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
                "s3",
                endpoint_url=self._endpoint_url,
                region_name=self._region_name,
            )
            self._value = cast(S3ClientPort, value)
        return self._value

    def head_object(self, **kwargs: str) -> dict[str, object]:
        return self._client().head_object(**kwargs)

    def get_object(self, **kwargs: str) -> dict[str, object]:
        return self._client().get_object(**kwargs)


class S3ReadConnector:
    @classmethod
    def from_default_credential_chain(
        cls,
        *,
        allowed_bucket: str,
        allowed_prefix: str,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        connector_id: str = "private-object-store-readonly",
    ) -> S3ReadConnector:
        return cls(
            LazyBoto3S3Client(
                endpoint_url=endpoint_url,
                region_name=region_name,
            ),
            allowed_bucket=allowed_bucket,
            allowed_prefix=allowed_prefix,
            connector_id=connector_id,
        )

    def __init__(
        self,
        client: S3ClientPort,
        *,
        allowed_bucket: str,
        allowed_prefix: str,
        connector_id: str = "private-object-store-readonly",
    ) -> None:
        self._client = client
        self._bucket = allowed_bucket
        self._prefix = allowed_prefix
        self._capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind="S3",
            driver_version="boto3-compatible:1",
            operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
            auth_modes=("IAM_ROLE", "OIDC"),
            native_version_kinds=(NativeVersionKind.VERSION_ID, NativeVersionKind.ETAG),
            checkpoint_kind="CONTINUATION_TOKEN",
            egress_class="ALLOWLISTED_EXTERNAL",
            selector_contract=ConnectorSelectorContract(
                fields=(
                    SelectorFieldSpec(name="bucket", value_type="STRING"),
                    SelectorFieldSpec(name="key", value_type="STRING"),
                    SelectorFieldSpec(
                        name="version_id", value_type="STRING", required=False
                    ),
                ),
            ),
        )

    @property
    def capability(self) -> ConnectorCapability:
        return self._capability

    def _location(self, request: ConnectorAccessRequest) -> tuple[str, str, str | None]:
        bucket = selector_text(request.selector, "bucket")
        key = selector_text(request.selector, "key")
        version_id = optional_text(request.selector, "version_id")
        if bucket != self._bucket or not key.startswith(self._prefix):
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "S3 object outside prefix")
        return bucket, key, version_id

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        bucket, key, version_id = self._location(request)
        args = {"Bucket": bucket, "Key": key}
        if version_id is not None:
            args["VersionId"] = version_id
        try:
            metadata = self._client.head_object(**args)
        except Exception as exc:
            raise ConnectorFailure(ConnectorErrorCode.DRIVER_ERROR, "S3 head failed") from exc
        effective_version = cast(str | None, metadata.get("VersionId")) or version_id
        etag = str(metadata.get("ETag", "")).strip('"') or None
        version = (
            NativeVersion(kind=NativeVersionKind.VERSION_ID, value=effective_version)
            if effective_version
            else NativeVersion(kind=NativeVersionKind.ETAG, value=etag)
            if etag
            else NativeVersion(kind=NativeVersionKind.NONE)
        )
        return (
            ConnectorArtifactRef(
                source_uri=f"s3://{bucket}/{key}",
                locator={"bucket": bucket, "key": key},
                media_type=str(metadata.get("ContentType", "application/octet-stream")),
                native_version=version,
                size_hint=cast(int | None, metadata.get("ContentLength")),
                observed_at=utc_now(),
            ),
        )

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del checkpoint
        bucket, key, requested_version = self._location(request)
        args = {"Bucket": bucket, "Key": key}
        version = (
            ref.native_version.value
            if ref.native_version.kind == NativeVersionKind.VERSION_ID
            else requested_version
        )
        if version:
            args["VersionId"] = version
        try:
            response = self._client.get_object(**args)
            body = cast(StreamingBodyPort, response["Body"])
            raw = body.read(request.max_bytes + 1)
        except Exception as exc:
            raise ConnectorFailure(ConnectorErrorCode.DRIVER_ERROR, "S3 read failed") from exc
        if len(raw) > request.max_bytes:
            raise ConnectorFailure(ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED, "S3 object too large")
        return ConnectorFetchResult(ref=ref, raw=raw, content_sha256=content_digest(raw))

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
