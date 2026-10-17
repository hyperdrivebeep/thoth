"""Validate and atomically stage research uploads in the project inbox."""

from __future__ import annotations

import re
from collections.abc import Callable
from hashlib import sha256
from os import replace
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request, UploadFile

from thoth.adapters.http.hosted_review import hosted_mode
from thoth.apps.hosted_review_quota import (
    HostedReviewLimits,
    inbox_nbytes,
    reject_if_upload_exceeds,
)
from thoth.domain.upload_scope import project_upload_prefix
from thoth.ports.auth import HttpAuthenticationPort
from thoth.protocol.jsonrpc import RpcApplicationError

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MEDIA_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".hwpx": "application/vnd.hancom.hwpx+zip",
    ".json": "application/json",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".yaml": "text/plain",
    ".yml": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


async def stage_file_upload(
    auth: HttpAuthenticationPort | None,
    request: Request,
    file: UploadFile,
    *,
    _workspace_path_for_request: Callable[[Request], Path],
) -> dict[str, object]:
    workspace = _workspace_path_for_request(request)
    authenticated_project_id = request.headers.get("x-thoth-project-id", "")
    if not authenticated_project_id:
        raise HTTPException(status_code=400, detail="x-thoth-project-id is required")
    if auth is not None:
        try:
            authenticated = await auth.authenticate_http(
                authorization=request.headers.get("authorization"),
                project_id=authenticated_project_id,
                method="project/source/connect",
                requested_scope={},
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        authenticated_project_id = authenticated.project_id
    filename = Path(file.filename or "upload.bin").name
    suffix = Path(filename).suffix.lower()
    media_type = MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise HTTPException(status_code=415, detail="unsupported research file type")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="empty research file")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="research file exceeds 64 MiB")
    if hosted_mode():
        try:
            reject_if_upload_exceeds(
                existing_bytes=inbox_nbytes(workspace),
                incoming_bytes=len(payload),
                limits=HostedReviewLimits(),
            )
        except RpcApplicationError as exc:
            raise HTTPException(status_code=413, detail=exc.message) from exc
    safe_name = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", filename).strip(".-")
    if not safe_name:
        safe_name = f"source{suffix}"
    safe_stem = Path(safe_name).stem[:48] or "source"
    safe_name = f"{safe_stem}{suffix}"
    digest = sha256(payload).hexdigest()
    relative_inbox = project_upload_prefix(authenticated_project_id)
    inbox = workspace / "inbox" / relative_inbox
    inbox.mkdir(parents=True, exist_ok=True)
    destination = inbox / f"{digest[:16]}-{safe_name}"
    if destination.exists():
        if sha256(destination.read_bytes()).hexdigest() != digest:
            raise HTTPException(status_code=409, detail="staged digest collision")
    else:
        temporary = inbox / f".{digest[:16]}-{uuid4().hex}.tmp"
        try:
            temporary.write_bytes(payload)
            if sha256(temporary.read_bytes()).hexdigest() != digest:
                raise HTTPException(status_code=500, detail="staged file verification failed")
            replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    relative = destination.relative_to(workspace / "inbox")
    return {
        "relative_path": relative.as_posix(),
        "filename": filename,
        "media_type": media_type,
        "byte_sha256": digest,
        "bytes": len(payload),
    }
