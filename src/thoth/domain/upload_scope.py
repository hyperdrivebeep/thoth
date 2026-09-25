from __future__ import annotations

import hashlib
from pathlib import PurePosixPath


def project_upload_prefix(project_id: str) -> str:
    digest = hashlib.sha256(project_id.encode()).hexdigest()[:24]
    return f"web/projects/{digest}"


def project_upload_path_allowed(project_id: str, relative_path: str) -> bool:
    raw = relative_path.replace("\\", "/")
    if any(part in {".", ".."} for part in raw.split("/")):
        return False
    path = PurePosixPath(raw)
    prefix = PurePosixPath(project_upload_prefix(project_id))
    return not path.is_absolute() and path != prefix and path.is_relative_to(prefix)
