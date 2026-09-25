from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from thoth.adapters.http.app import create_app


@pytest.mark.asyncio
async def test_file_stage_writes_content_addressed_inbox_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/files/stage",
            files={"file": ("../계획서.md", b"# approved plan\n", "text/markdown")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["media_type"] == "text/markdown"
    assert ".." not in payload["relative_path"]
    staged = workspace / "inbox" / payload["relative_path"]
    assert staged.read_bytes() == b"# approved plan\n"


@pytest.mark.asyncio
async def test_file_stage_bounds_name_and_reuses_verified_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    transport = httpx.ASGITransport(app=create_app())
    name = f"{'a' * 300}.md"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/files/stage",
            files={"file": (name, b"bounded", "text/markdown")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )
        second = await client.post(
            "/files/stage",
            files={"file": (name, b"bounded", "text/markdown")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["relative_path"] == second.json()["relative_path"]
    assert len(Path(first.json()["relative_path"]).name) <= 16 + 1 + 96 + 3


@pytest.mark.asyncio
async def test_file_stage_rejects_unsupported_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "workspace"))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/files/stage",
            files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )

    assert response.status_code == 415


@pytest.mark.asyncio
async def test_file_stage_rejects_corrupt_existing_digest_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/files/stage",
            files={"file": ("plan.md", b"trusted", "text/markdown")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )
        staged = workspace / "inbox" / first.json()["relative_path"]
        staged.write_bytes(b"corrupt")
        second = await client.post(
            "/files/stage",
            files={"file": ("plan.md", b"trusted", "text/markdown")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )

    assert second.status_code == 409
