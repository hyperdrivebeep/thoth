from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from thoth.adapters.connectors import (
    GitReadConnector,
    LocalFileConnector,
    McpResourceConnector,
    PostgresReadConnector,
    S3ReadConnector,
)
from thoth.domain.connectors import ConnectorAccessRequest, ConnectorErrorCode, ConnectorFailure
from thoth.domain.enums import AuthorityState, CutoffState, SecurityClass


def access(connector_id: str, selector: dict[str, JsonValue]) -> ConnectorAccessRequest:
    return ConnectorAccessRequest(
        actor_id="agent:test",
        project_id="project:test",
        connector_id=connector_id,
        selector=selector,
        authority=AuthorityState.OFFICIAL,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        cutoff_at=datetime(2026, 8, 31, tzinfo=UTC),
        policy_id="policy:test:v1",
        policy_revision=1,
        policy_digest="a" * 64,
    )


@pytest.mark.asyncio
async def test_local_connector_is_bounded_and_content_addressed(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    (root / "plan.md").write_bytes(b"# Plan\n")
    connector = LocalFileConnector(root)
    request = access("local-file-upload", {"relative_path": "plan.md"})

    refs = await connector.discover(request)
    result = await connector.fetch(request, refs[0])

    assert result.raw == b"# Plan\n"
    assert (
        result.content_sha256 == "c3964bb3b70a957ec9b233c7dd3653f6ba17701ab00facf88ae1393dc6155577"
    )
    assert refs[0].native_version.value is not None

    with pytest.raises(ConnectorFailure) as caught:
        await connector.discover(access("local-file-upload", {"relative_path": "../outside.md"}))
    assert caught.value.code == ConnectorErrorCode.SCOPE_DENIED


@pytest.mark.asyncio
async def test_git_connector_pins_commit_and_never_reads_worktree_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repos" / "sample"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "qa@example.test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "THOTH QA"], check=True)
    target = repo / "evidence.txt"
    target.write_text("sealed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "evidence.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    connector = GitReadConnector(tmp_path / "repos")
    request = access(
        "read-only-git-snapshot",
        {
            "repository_path": "sample",
            "revision": "HEAD",
            "file_path": "evidence.txt",
        },
    )
    ref = (await connector.discover(request))[0]
    target.write_text("uncommitted drift\n", encoding="utf-8")

    fetched = await connector.fetch(request, ref)

    assert fetched.raw == b"sealed\n"
    assert len(ref.native_version.value or "") == 40


class FakeBody:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def read(self, amt: int | None = None) -> bytes:
        return self.raw if amt is None else self.raw[:amt]


class FakeS3:
    def head_object(self, **kwargs: str) -> dict[str, object]:
        assert kwargs == {"Bucket": "evidence", "Key": "project-a/result.json"}
        return {
            "VersionId": "version-7",
            "ETag": '"etag-7"',
            "ContentType": "application/json",
            "ContentLength": 12,
        }

    def get_object(self, **kwargs: str) -> dict[str, object]:
        assert kwargs["VersionId"] == "version-7"
        return {"Body": FakeBody(b'{"ok": true}')}


@pytest.mark.asyncio
async def test_s3_connector_preserves_native_version_and_prefix_scope() -> None:
    connector = S3ReadConnector(FakeS3(), allowed_bucket="evidence", allowed_prefix="project-a/")
    request = access(
        "private-object-store-readonly",
        {"bucket": "evidence", "key": "project-a/result.json"},
    )
    ref = (await connector.discover(request))[0]
    result = await connector.fetch(request, ref)

    assert ref.native_version.value == "version-7"
    assert result.raw == b'{"ok": true}'

    with pytest.raises(ConnectorFailure) as caught:
        await connector.discover(
            access(
                "private-object-store-readonly",
                {"bucket": "evidence", "key": "project-b/result.json"},
            )
        )
    assert caught.value.code == ConnectorErrorCode.SCOPE_DENIED


class FakeMcp:
    async def metadata(self, uri: str) -> dict[str, object]:
        assert uri == "project://a/report"
        return {
            "mimeType": "text/plain",
            "lastModified": "2026-08-31T00:00:00Z",
        }

    async def read(self, uri: str) -> tuple[bytes, str]:
        assert uri == "project://a/report"
        return b"official evidence", "text/plain"


@pytest.mark.asyncio
async def test_mcp_connector_reseals_resource_in_thoth_scope() -> None:
    connector = McpResourceConnector(FakeMcp(), allowed_uri_prefixes=("project://a/",))
    request = access("mcp-resource-readonly", {"uri": "project://a/report"})
    ref = (await connector.discover(request))[0]
    result = await connector.fetch(request, ref)

    assert ref.native_version.value == "2026-08-31T00:00:00Z"
    assert result.raw == b"official evidence"


class FakePostgres(PostgresReadConnector):
    def _execute(self, query: str, max_bytes: int) -> bytes:
        assert max_bytes > 0
        assert query == 'SELECT * FROM "approved_metrics" LIMIT 100'
        return b'[{"metric":"accuracy","value":0.94}]'


@pytest.mark.asyncio
async def test_postgres_connector_enforces_view_and_read_only_query_shape() -> None:
    connector = FakePostgres(
        "postgresql+psycopg://credential-is-adapter-owned",
        allowed_views=("approved_metrics",),
    )
    request = access(
        "postgres-readonly",
        {
            "view": "approved_metrics",
            "limit": 100,
        },
    )
    ref = (await connector.discover(request))[0]
    result = await connector.fetch(request, ref)

    assert result.raw.startswith(b"[")
    with pytest.raises(ConnectorFailure) as caught:
        await connector.discover(
            access(
                "postgres-readonly",
                {
                    "view": "approved_metrics",
                    "query": "DELETE FROM approved_metrics",
                },
            )
        )
    assert caught.value.code == ConnectorErrorCode.SCOPE_DENIED
    with pytest.raises(ValueError, match="safe schema-qualified identifiers"):
        FakePostgres(
            "postgresql+psycopg://credential-is-adapter-owned",
            allowed_views=('approved"; DROP TABLE evidence; --',),
        )
