"""Characterize staging authorization, limits and temporary-file cleanup."""

from __future__ import annotations

import importlib
import json
import shutil
import stat
import time
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request, UploadFile
from tests.contract.test_async_http import StaticActorAuth

from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.upload_scope import project_upload_prefix

http_app = importlib.import_module("thoth.adapters.http.app")
LIMIT = 64 * 1024 * 1024


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("THOTH_WEB_DIST", str(tmp_path / "missing-dist"))
    monkeypatch.delenv("THOTH_REVIEW_SESSION_ID", raising=False)
    monkeypatch.delenv("THOTH_REVIEW_SESSION_SECRET", raising=False)


def request(project: str = "project:stage") -> Request:
    return Request({"type": "http", "headers": [(b"x-thoth-project-id", project.encode())]})


class ObservedUpload(UploadFile):
    def __init__(self, payload: bytes, events: list[str], filename: str = "plan.md") -> None:
        super().__init__(file=BytesIO(payload), filename=filename)
        self.events = events
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.events.append("payload-read")
        self.read_sizes.append(size)
        return await super().read(size)


class ObservedAuth(StaticActorAuth):
    def __init__(self, events: list[str], *, denied: bool = False) -> None:
        super().__init__({})
        self.events = events
        self.denied = denied

    async def authenticate_http(
        self,
        *,
        authorization: str | None,
        project_id: str,
        method: str,
        requested_scope: dict[str, str],
    ) -> AuthenticatedActorContext:
        assert authorization is None and project_id == "project:stage"
        assert method == "project/source/connect" and requested_scope == {}
        self.events.append("authenticate")
        if self.denied:
            raise PermissionError("AUTH_SESSION_REQUIRED")
        return AuthenticatedActorContext(
            actor_id="human:stage",
            session_id="session:stage",
            project_id="project:authenticated",
            role_assignment_id="role:stage",
            role="researcher",
            capabilities=("READ", "WRITE"),
            data_scopes=("PROJECT",),
        )


@pytest.mark.parametrize("case", ("missing-project", "denied-auth", "unsupported"))
async def test_staging_checks_before_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    events: list[str] = []

    def workspace(_request: Request) -> Path:
        events.append("workspace")
        return tmp_path

    monkeypatch.setattr(http_app, "_workspace_path_for_request", workspace)
    upload = ObservedUpload(
        b"source", events, "payload.exe" if case == "unsupported" else "plan.md"
    )
    auth = ObservedAuth(events, denied=case == "denied-auth")
    try:
        with pytest.raises(HTTPException) as raised:
            await http_app._stage_file(
                auth, request("" if case == "missing-project" else "project:stage"), upload
            )
        expected = {
            "missing-project": (400, "x-thoth-project-id is required", ["workspace"]),
            "denied-auth": (403, "AUTH_SESSION_REQUIRED", ["workspace", "authenticate"]),
            "unsupported": (415, "unsupported research file type", ["workspace", "authenticate"]),
        }[case]
        assert (raised.value.status_code, raised.value.detail, events) == expected
        assert upload.read_sizes == [] and not (tmp_path / "inbox").exists()
    finally:
        await upload.close()


@pytest.mark.parametrize("size", (0, LIMIT + 1))
async def test_empty_and_oversized_payload_stop_before_inbox(tmp_path: Path, size: int) -> None:
    events: list[str] = []
    upload = ObservedUpload(b"x" * size, events)
    try:
        with pytest.raises(HTTPException) as raised:
            await http_app._stage_file(None, request(), upload)
        assert raised.value.status_code == (400 if size == 0 else 413)
        assert raised.value.detail == (
            "empty research file" if size == 0 else "research file exceeds 64 MiB"
        )
        assert events == ["payload-read"] and upload.read_sizes == [LIMIT + 1]
        assert not (tmp_path / "inbox").exists()
    finally:
        await upload.close()


async def test_authenticated_project_and_late_workspace_callback_are_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    app = cast(FastAPI, http_app.create_app(auth=ObservedAuth(events)))
    selected = tmp_path / "selected-after-app-construction"

    def workspace(_request: Request) -> Path:
        events.append("workspace")
        return selected

    monkeypatch.setattr(http_app, "_workspace_path_for_request", workspace)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as client:
        response = await client.post(
            "/files/stage",
            headers={"x-thoth-project-id": "project:stage"},
            files={"file": ("plan.md", b"evidence")},
        )
    assert response.status_code == 200
    result = response.json()
    relative = Path(result["relative_path"])
    assert relative.parent.as_posix() == project_upload_prefix("project:authenticated")
    assert (selected / "inbox" / relative).read_bytes() == b"evidence"
    assert events == ["workspace", "authenticate"]
    assert not (tmp_path / "inbox").exists() and app.state.runtime is None


async def test_hosted_transport_denial_precedes_helper_and_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "HOSTED_REVIEW")
    app = cast(FastAPI, http_app.create_app())

    def forbidden(_request: Request) -> Path:
        pytest.fail("unauthorized transport reached staging helper")

    monkeypatch.setattr(http_app, "_workspace_path_for_request", forbidden)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as client:
        response = await client.post(
            "/files/stage",
            headers={"x-thoth-project-id": "project:stage"},
            files={"file": ("plan.md", b"evidence")},
        )
    assert response.status_code == 403 and response.json() == {
        "detail": "HOSTED_REVIEW_SESSION_REQUIRED"
    }
    assert not (tmp_path / "inbox").exists() and app.state.runtime is None


@pytest.mark.parametrize("failure", ("none", "write", "verify", "rename"))
async def test_temporary_write_verification_atomic_replace_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    events: list[str] = []
    payload = b"evidence"
    digest = sha256(payload).hexdigest()
    inbox = tmp_path / "inbox" / project_upload_prefix("project:stage")
    destination = inbox / f"{digest[:16]}-plan.md"
    untouched = tmp_path / "unrelated.md"
    untouched.write_bytes(b"keep")
    write, read, unlink = Path.write_bytes, Path.read_bytes, Path.unlink
    temporary_paths: list[Path] = []

    def write_bytes(path: Path, data: bytes) -> int:
        if path.suffix == ".tmp":
            assert path.parent == inbox and not destination.exists()
            temporary_paths.append(path)
            events.append("temporary-write")
            written = write(path, data)
            if failure == "write":
                raise OSError("injected write failure after partial staging")
            return written
        return write(path, data)

    def read_bytes(path: Path) -> bytes:
        if path.suffix == ".tmp":
            events.append("temporary-verify")
            assert read(path) == payload and not destination.exists()
            if failure == "verify":
                return b"corrupted temporary bytes"
            if failure == "rename":
                destination.mkdir()
                (destination / "keep.txt").write_bytes(b"concurrent content")
        return read(path)

    def cleanup(path: Path, missing_ok: bool = False) -> None:
        if path.suffix == ".tmp":
            assert missing_ok
            events.append("cleanup")
            if failure == "none":
                assert not path.exists() and read(destination) == payload
            else:
                assert path.exists()
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "unlink", cleanup)
    upload = ObservedUpload(payload, events)
    try:
        if failure == "none":
            result = await http_app._stage_file(None, request(), upload)
            assert result == {
                "relative_path": destination.relative_to(tmp_path / "inbox").as_posix(),
                "filename": "plan.md",
                "media_type": "text/markdown",
                "byte_sha256": digest,
                "bytes": len(payload),
            }
        elif failure == "verify":
            with pytest.raises(HTTPException) as raised:
                await http_app._stage_file(None, request(), upload)
            assert (
                raised.value.status_code == 500
                and raised.value.detail == "staged file verification failed"
            )
        else:
            with pytest.raises(OSError):
                await http_app._stage_file(None, request(), upload)
        assert events == (
            ["payload-read", "temporary-write", "cleanup"]
            if failure == "write"
            else ["payload-read", "temporary-write", "temporary-verify", "cleanup"]
        )
        assert len(temporary_paths) == 1 and not temporary_paths[0].exists()
        assert read(untouched) == b"keep"
        if failure in {"write", "verify"}:
            assert not destination.exists()
        if failure == "rename":
            assert read(destination / "keep.txt") == b"concurrent content"
    finally:
        await upload.close()


async def test_hosted_quota_is_checked_after_read_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "HOSTED_REVIEW")
    monkeypatch.setenv("THOTH_REVIEW_SESSION_ID", "fixed-test")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    existing = inbox / "quota-existing.dat"
    started = time.perf_counter()
    free_before = shutil.disk_usage(tmp_path).free
    with existing.open("wb") as handle:
        handle.truncate(200 * 1024 * 1024)
    metadata = existing.stat()
    print(
        json.dumps(
            {
                "quota_fixture_seconds": time.perf_counter() - started,
                "logical_bytes": metadata.st_size,
                "sparse": bool(metadata.st_file_attributes & stat.FILE_ATTRIBUTE_SPARSE_FILE),
                "volume_free_delta_bytes_approx": free_before - shutil.disk_usage(tmp_path).free,
            }
        )
    )
    events: list[str] = []
    upload = ObservedUpload(b"one more", events)
    try:
        with pytest.raises(HTTPException) as raised:
            await http_app._stage_file(None, request(), upload)
        assert (
            raised.value.status_code == 413 and raised.value.detail == "HOSTED_REVIEW_UPLOAD_LIMIT"
        )
        assert events == ["payload-read"] and upload.read_sizes == [LIMIT + 1]
        assert list(inbox.iterdir()) == [existing] and existing.stat().st_size == 200 * 1024 * 1024
    finally:
        await upload.close()


@pytest.mark.parametrize("corrupt", (False, True))
async def test_existing_digest_replay_and_collision_never_create_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: bool
) -> None:
    upload = ObservedUpload(b"evidence", [])
    try:
        first = await http_app._stage_file(None, request(), upload)
    finally:
        await upload.close()
    destination = tmp_path / "inbox" / str(first["relative_path"])
    if corrupt:
        destination.write_bytes(b"preserve corrupt original")
    original_bytes = destination.read_bytes()
    writes: list[Path] = []
    reads: list[Path] = []
    write, read = Path.write_bytes, Path.read_bytes

    def observed_write(path: Path, data: bytes) -> int:
        writes.append(path)
        return write(path, data)

    def observed_read(path: Path) -> bytes:
        reads.append(path)
        return read(path)

    monkeypatch.setattr(Path, "write_bytes", observed_write)
    monkeypatch.setattr(Path, "read_bytes", observed_read)
    replay = ObservedUpload(b"evidence", [])
    try:
        if corrupt:
            with pytest.raises(HTTPException) as raised:
                await http_app._stage_file(None, request(), replay)
            assert (
                raised.value.status_code == 409 and raised.value.detail == "staged digest collision"
            )
        else:
            assert await http_app._stage_file(None, request(), replay) == first
        assert writes == [] and reads == [destination]
        assert read(destination) == original_bytes
        assert not list((tmp_path / "inbox").rglob("*.tmp"))
    finally:
        await replay.close()
