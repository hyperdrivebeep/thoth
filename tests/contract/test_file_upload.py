from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from thoth.adapters.http.app import create_app
from thoth.adapters.http.file_staging import MAX_UPLOAD_BYTES, MEDIA_TYPES, UPLOAD_ERROR_HEADER


@pytest.mark.asyncio
async def test_file_stage_writes_content_addressed_inbox_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
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
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
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
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/files/stage",
            files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )

    assert response.status_code == 415
    # The sentence is unchanged; the reason also travels as a code the screen can translate.
    assert response.json() == {"detail": "unsupported research file type"}
    assert response.headers["x-thoth-upload-error"] == "UPLOAD_UNSUPPORTED_TYPE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "status", "detail", "code"),
    [
        (0, 400, "empty research file", "UPLOAD_EMPTY"),
        (MAX_UPLOAD_BYTES + 1, 413, "research file exceeds 64 MiB", "UPLOAD_TOO_LARGE"),
    ],
)
async def test_file_stage_names_the_reason_for_an_empty_or_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int, status: int, detail: str, code: str
) -> None:
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "workspace"))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/files/stage",
            files={"file": ("notes.md", b"x" * size, "text/markdown")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )

    assert (response.status_code, response.json()) == (status, {"detail": detail})
    assert response.headers[UPLOAD_ERROR_HEADER] == code


def test_the_file_picker_offers_exactly_the_types_the_server_stages() -> None:
    """The screen filters the file dialog and refuses a wrong type before sending it. Its list is a
    copy of the server's, so the two are compared here."""
    source = (
        Path(__file__).parents[2] / "apps" / "web" / "src" / "api" / "uploadTypes.ts"
    ).read_text(encoding="utf-8")
    listed = re.search(r"UPLOAD_EXTENSIONS = \[([^\]]*)\]", source)
    limit = re.search(r"MAX_UPLOAD_BYTES = (\d+) \* 1024 \* 1024;", source)
    assert listed is not None and limit is not None
    assert sorted(re.findall(r'"(\.[a-z]+)"', listed.group(1))) == sorted(MEDIA_TYPES)
    assert int(limit.group(1)) * 1024 * 1024 == MAX_UPLOAD_BYTES


@pytest.mark.asyncio
async def test_file_stage_rejects_corrupt_existing_digest_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["30_RESULT.yaml", "plan.YML"])
async def test_file_stage_takes_yaml_as_plain_text_and_keeps_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    transport = httpx.ASGITransport(app=create_app())
    body = b"identity: example\nnumerator: 19\n"
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/files/stage",
            files={"file": (name, body, "application/octet-stream")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["media_type"] == "text/plain" and payload["filename"] == name
    assert payload["relative_path"].lower().endswith(name.lower())
    assert (workspace / "inbox" / payload["relative_path"]).read_bytes() == body


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["settings.toml", "notes.yaml.exe", "config.ini", "payload.yamlx"])
async def test_file_stage_still_rejects_other_types_next_to_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "workspace"))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/files/stage",
            files={"file": (name, b"a: 1\n", "text/plain")},
            headers={"x-thoth-project-id": "project:file-upload"},
        )

    assert response.status_code == 415
