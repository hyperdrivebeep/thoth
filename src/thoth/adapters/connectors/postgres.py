from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable

from sqlalchemy import Engine, create_engine, text

from thoth.adapters.connectors.common import content_digest, selector_text, utc_now
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

_VIEW_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")


class PostgresReadConnector:
    def __init__(
        self,
        dsn: str,
        *,
        allowed_views: tuple[str, ...],
        engine_factory: Callable[[str], Engine] = create_engine,
        connector_id: str = "postgres-readonly",
    ) -> None:
        self._dsn = dsn
        if not allowed_views or any(_VIEW_NAME.fullmatch(value) is None for value in allowed_views):
            raise ValueError("allowed PostgreSQL views must be safe schema-qualified identifiers")
        self._allowed_views = frozenset(allowed_views)
        self._engine_factory = engine_factory
        self._capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind="POSTGRES",
            driver_version="sqlalchemy:2",
            operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
            auth_modes=("DB_ROLE", "MTLS"),
            native_version_kinds=(NativeVersionKind.SNAPSHOT, NativeVersionKind.CONTENT_HASH),
            checkpoint_kind="QUERY_DIGEST",
            egress_class="INTRANET",
            selector_contract=ConnectorSelectorContract(
                fields=(
                    SelectorFieldSpec(name="view", value_type="STRING"),
                    SelectorFieldSpec(name="limit", value_type="INTEGER", required=False),
                ),
            ),
        )

    @property
    def capability(self) -> ConnectorCapability:
        return self._capability

    def _query(self, request: ConnectorAccessRequest) -> tuple[str, str]:
        view = selector_text(request.selector, "view")
        if "query" in request.selector:
            raise ConnectorFailure(
                ConnectorErrorCode.SCOPE_DENIED,
                "raw SQL is forbidden; select an approved view",
            )
        if view not in self._allowed_views:
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "view is outside read policy")
        limit_value = request.selector.get("limit", 10_000)
        if not isinstance(limit_value, int) or isinstance(limit_value, bool):
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "limit must be an integer")
        if limit_value < 1 or limit_value > 100_000:
            raise ConnectorFailure(ConnectorErrorCode.SCOPE_DENIED, "limit is outside policy")
        quoted_view = ".".join(f'"{part}"' for part in view.split("."))
        query = f"SELECT * FROM {quoted_view} LIMIT {limit_value}"
        return view, query

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        view, query = self._query(request)
        query_digest = content_digest(query.encode())
        return (
            ConnectorArtifactRef(
                source_uri=f"postgres://approved-view/{view}",
                locator={"view": view, "query_digest": query_digest},
                media_type="application/json",
                native_version=NativeVersion(
                    kind=NativeVersionKind.SNAPSHOT,
                    value=query_digest,
                ),
                observed_at=utc_now(),
            ),
        )

    def _execute(self, query: str, max_bytes: int) -> bytes:
        engine = self._engine_factory(self._dsn)
        payload = bytearray(b"[")
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
                    streaming = connection.execution_options(stream_results=True, yield_per=1)
                    result = streaming.execute(text(query))
                    for row in result.mappings():
                        encoded = json.dumps(
                            dict(row),
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode()
                        separator = b"" if len(payload) == 1 else b", "
                        if len(payload) + len(separator) + len(encoded) + 1 > max_bytes:
                            raise ConnectorFailure(
                                ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED,
                                "query result too large",
                            )
                        payload.extend(separator)
                        payload.extend(encoded)
                    if len(payload) + 1 > max_bytes:
                        raise ConnectorFailure(
                            ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED,
                            "query result too large",
                        )
                    payload.extend(b"]")
                    transaction.rollback()
                except Exception:
                    transaction.rollback()
                    raise
        finally:
            engine.dispose()
        return bytes(payload)

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del checkpoint
        _, query = self._query(request)
        try:
            raw = await asyncio.to_thread(self._execute, query, request.max_bytes)
        except ConnectorFailure:
            raise
        except Exception as exc:
            raise ConnectorFailure(ConnectorErrorCode.DRIVER_ERROR, "database read failed") from exc
        if len(raw) > request.max_bytes:
            raise ConnectorFailure(
                ConnectorErrorCode.CONTENT_LIMIT_EXCEEDED, "query result too large"
            )
        return ConnectorFetchResult(ref=ref, raw=raw, content_sha256=content_digest(raw))

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
