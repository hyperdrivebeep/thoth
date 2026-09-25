from datetime import UTC, datetime

import pytest

from thoth.adapters.connectors.common import utc_now
from thoth.adapters.connectors.project_public_web import ProjectPublicWebConnector
from thoth.adapters.connectors.public_web import PublicWebConnector
from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.web_acquisition import WebPage


class RecordingReader:
    def __init__(self, hosts: tuple[str, ...]) -> None:
        self.hosts = hosts
        self.calls: list[str] = []

    async def read(self, uri: str, *, max_bytes: int, timeout: float) -> WebPage:
        self.calls.append(uri)
        return WebPage(
            requested_uri=uri,
            final_uri=uri,
            media_type="text/html",
            raw=b'<a href="/abs/2205.14135">paper</a>',
            retrieved_at=utc_now(),
        )


class _ExposedProjectPublicWebConnector(ProjectPublicWebConnector):
    def bound_for_test(self, request: ConnectorAccessRequest) -> PublicWebConnector:
        return self._bound(request)


@pytest.mark.asyncio
async def test_project_hosts_are_not_reused_across_projects() -> None:
    seen: dict[str, RecordingReader] = {}

    def hosts_for(project_id: str) -> tuple[str, ...]:
        return {"project:a": ("arxiv.org",), "project:b": ("example.org",)}[project_id]

    def factory(hosts: tuple[str, ...]) -> RecordingReader:
        reader = RecordingReader(hosts)
        seen[",".join(hosts)] = reader
        return reader

    connector = _ExposedProjectPublicWebConnector(hosts_for, reader_factory=factory)
    first = await connector.discover(
        ConnectorAccessRequest.model_validate(
            {
                "actor_id": "actor",
                "project_id": "project:a",
                "connector_id": "project-public-web",
                "operation": "DISCOVER",
                "selector": {
                    "mode": "SITE_DISCOVER",
                    "entrypoint_id": "arxiv:cs.LG:recent",
                },
                "policy_id": "policy:a",
                "policy_revision": 1,
                "policy_digest": "a" * 64,
                "cutoff_at": datetime(2026, 9, 20, tzinfo=UTC).isoformat(),
            }
        )
    )
    second = connector.bound_for_test(
        ConnectorAccessRequest.model_validate(
            {
                "actor_id": "actor",
                "project_id": "project:b",
                "connector_id": "project-public-web",
                "operation": "DISCOVER",
                "selector": {"mode": "READ", "uri": "https://example.org/paper"},
                "policy_id": "policy:b",
                "policy_revision": 1,
                "policy_digest": "b" * 64,
            }
        )
    )
    assert [ref.source_uri for ref in first] == ["https://arxiv.org/abs/2205.14135"]
    assert seen["arxiv.org"].hosts == ("arxiv.org",)
    assert second.reader is seen["example.org"]
    assert seen["example.org"].hosts == ("example.org",)
    assert seen["arxiv.org"] is not second.reader
