"""Internal wording never leaves the workspace through the managed web route."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from thoth.adapters.connectors.common import utc_now
from thoth.adapters.connectors.project_public_web import ProjectPublicWebConnector
from thoth.adapters.connectors.public_web import PublicWebConnector
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorErrorCode,
    ConnectorFailure,
    NativeVersion,
    NativeVersionKind,
)
from thoth.domain.enums import SecurityClass
from thoth.domain.web_acquisition import WebPage

SENTINEL = "INTERNAL-QUESTION-SENTINEL-77aa"
_LISTING = (
    b'<a href="/abs/2609.00001">FlashAttention follow-up</a>'
    b'<a href="https://html.duckduckgo.com/html/?q=leak">off-host</a>'
    b'<a href="/abs/2609.00002?utm_source=listing">tracked</a>'
)


class _Reader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def read(self, uri: str, *, max_bytes: int, timeout: float) -> WebPage:
        del max_bytes, timeout
        self.calls.append(uri)
        return WebPage(
            requested_uri=uri,
            final_uri=uri,
            media_type="text/html",
            raw=_LISTING,
            retrieved_at=utc_now(),
        )


class _ExposedProjectPublicWebConnector(ProjectPublicWebConnector):
    def bound_for_test(self, request: ConnectorAccessRequest) -> PublicWebConnector:
        return self._bound(request)


def _connector(reader: _Reader) -> _ExposedProjectPublicWebConnector:
    return _ExposedProjectPublicWebConnector(
        lambda _project_id: ("arxiv.org",),
        reader_factory=lambda _hosts: reader,
    )


def _request(
    selector: dict[str, JsonValue],
    *,
    query_security_class: SecurityClass | None = None,
) -> ConnectorAccessRequest:
    return ConnectorAccessRequest(
        actor_id="agent:research-discovery",
        project_id="project:disclosure",
        connector_id="project-public-web",
        selector=selector,
        security_class=SecurityClass.PUBLIC,
        query_security_class=query_security_class,
        cutoff_at=datetime(2026, 9, 20, tzinfo=UTC),
        policy_id="policy:disclosure:v1",
        policy_revision=1,
        policy_digest="a" * 64,
    )


@pytest.mark.asyncio
async def test_search_mode_is_rejected_before_any_io() -> None:
    reader = _Reader()
    with pytest.raises(ConnectorFailure) as error:
        await _connector(reader).discover(
            _request({"mode": "SEARCH", "query": SENTINEL})
        )
    assert error.value.code == ConnectorErrorCode.EGRESS_DENIED
    assert "PUBLIC_QUERY_DISCLOSURE_NOT_APPROVED" in str(error.value)
    assert reader.calls == []


@pytest.mark.parametrize(
    "uri",
    [
        f"https://arxiv.org/search?q={SENTINEL}",
        "https://arxiv.org/a/search",
        f"https://arxiv.org/abs/2609.00001?note={SENTINEL}",
    ],
)
@pytest.mark.asyncio
async def test_read_disguised_as_search_is_rejected_before_any_io(uri: str) -> None:
    reader = _Reader()
    with pytest.raises(ConnectorFailure) as error:
        await _connector(reader).discover(_request({"mode": "READ", "uri": uri}))
    assert error.value.code == ConnectorErrorCode.EGRESS_DENIED
    assert "PUBLIC_QUERY_DISCLOSURE_NOT_APPROVED" in str(error.value)
    assert reader.calls == []


@pytest.mark.asyncio
async def test_fetch_refuses_query_string_targets_before_any_io() -> None:
    reader = _Reader()
    uri = f"https://arxiv.org/search?q={SENTINEL}"
    ref = ConnectorArtifactRef(
        source_uri=uri,
        locator={"mode": "READ", "uri": uri},
        media_type="text/html",
        native_version=NativeVersion(kind=NativeVersionKind.NONE),
        observed_at=utc_now(),
    )
    with pytest.raises(ConnectorFailure) as error:
        await _connector(reader).fetch(_request({"mode": "READ", "uri": uri}), ref)
    assert error.value.code == ConnectorErrorCode.EGRESS_DENIED
    assert reader.calls == []


@pytest.mark.parametrize(
    "query_security_class", [None, SecurityClass.PUBLIC]
)
@pytest.mark.asyncio
async def test_query_class_omission_or_downgrade_is_not_permission(
    query_security_class: SecurityClass | None,
) -> None:
    reader = _Reader()
    with pytest.raises(ConnectorFailure):
        await _connector(reader).discover(
            _request(
                {"mode": "READ", "uri": f"https://arxiv.org/search?q={SENTINEL}"},
                query_security_class=query_security_class,
            )
        )
    assert reader.calls == []


@pytest.mark.asyncio
async def test_registered_site_discovery_sends_no_internal_text_or_search_host() -> None:
    reader = _Reader()
    refs = await _connector(reader).discover(
        _request({"mode": "SITE_DISCOVER", "entrypoint_id": "arxiv:cs.LG:recent"})
    )
    assert reader.calls == ["https://arxiv.org/list/cs.LG/recent"]
    assert all(SENTINEL not in call for call in reader.calls)
    assert all("duckduckgo" not in call for call in reader.calls)
    assert [ref.source_uri for ref in refs] == ["https://arxiv.org/abs/2609.00001"]


def test_bound_connector_keeps_search_disabled() -> None:
    connector = _connector(_Reader())
    bound = connector.bound_for_test(_request({"mode": "READ", "uri": "https://arxiv.org/abs/1"}))
    assert bound.search_enabled is False
    assert "query" not in {
        field.name for field in bound.capability.selector_contract.fields
    }
