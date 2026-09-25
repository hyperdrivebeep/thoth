from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from thoth.adapters.connectors.project_public_web import ProjectPublicWebConnector
from thoth.domain.connectors import ConnectorAccessRequest, ConnectorErrorCode, ConnectorFailure
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass
from thoth.domain.web_acquisition import WebPage


class _Reader:
    def __init__(self) -> None:
        self.uris: list[str] = []

    async def read(self, uri: str, *, max_bytes: int, timeout: float) -> WebPage:
        del max_bytes, timeout
        self.uris.append(uri)
        return WebPage(
            requested_uri=uri,
            final_uri=uri,
            media_type="text/html",
            raw=b'<a href="/abs/2609.00001">paper</a><a href="https://evil.example/x">bad</a>',
            retrieved_at=datetime(2026, 9, 20, tzinfo=UTC),
        )


def _request(selector: dict[str, JsonValue]) -> ConnectorAccessRequest:
    return ConnectorAccessRequest(
        actor_id="agent:test",
        project_id="project:test",
        connector_id="project-public-web",
        selector=selector,
        authority=AuthorityState.OFFICIAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.PUBLIC,
        cutoff_at=datetime(2026, 9, 20, tzinfo=UTC),
        policy_id="policy:test:v1",
        policy_revision=1,
        policy_digest="a" * 64,
    )


@pytest.mark.asyncio
async def test_registered_site_discovery_keeps_same_host_candidates() -> None:
    reader = _Reader()
    connector = ProjectPublicWebConnector(
        lambda _project_id: ("arxiv.org",),
        reader_factory=lambda _hosts: reader,
    )

    refs = await connector.discover(
        _request({"mode": "SITE_DISCOVER", "entrypoint_id": "arxiv:cs.LG:recent"})
    )

    assert reader.uris == ["https://arxiv.org/list/cs.LG/recent"]
    assert [item.source_uri for item in refs] == ["https://arxiv.org/abs/2609.00001"]


@pytest.mark.asyncio
async def test_free_form_public_query_is_denied() -> None:
    connector = ProjectPublicWebConnector(
        lambda _project_id: ("arxiv.org",),
        reader_factory=lambda _hosts: _Reader(),
    )

    with pytest.raises(ConnectorFailure) as error:
        await connector.discover(_request({"mode": "SEARCH", "query": "private question"}))

    assert error.value.code == ConnectorErrorCode.EGRESS_DENIED
