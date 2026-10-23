"""Classify injected location signals and enforce support policy before opening SQLite."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from thoth.adapters.storage.workspace_location_probes import LocationSignals, probe_workspace
from thoth.domain.workspace_identity import windows_path_key, workspace_identity
from thoth.domain.workspace_location import (
    LocationKind,
    WorkspaceLocation,
    WorkspaceLocationConfirmation,
    WorkspaceLocationError,
)

CONFIRMATION_FILE = "workspace-location.json"


def _contains(path: str, root: str, windows: bool) -> bool:
    if windows:
        selected, parent = (
            PureWindowsPath(windows_path_key(path)),
            PureWindowsPath(windows_path_key(root)),
        )
    else:
        selected, parent = PurePosixPath(path), PurePosixPath(root)
    return selected == parent or parent in selected.parents


def _mount_kind(path: str, mountinfo: str) -> tuple[LocationKind, str]:
    mounts: list[tuple[str, str]] = []
    try:
        for line in mountinfo.splitlines():
            if not line.strip():
                continue
            left, right = line.split(" - ", 1)
            mount = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), left.split()[4])
            if _contains(path, mount, False):
                mounts.append((mount, right.split()[0]))
    except (ValueError, IndexError):
        return "UNKNOWN", "MOUNTINFO_INVALID"
    if not mounts:
        return "UNKNOWN", "MOUNTINFO_UNAVAILABLE"
    _mount, kind = max(mounts, key=lambda item: len(PurePosixPath(item[0]).parts))
    if kind in {"9p", "drvfs"}:
        return "UNSUPPORTED_BRIDGE", "WINDOWS_LINUX_MOUNT"
    if kind.startswith("nfs") or kind in {"cifs", "smb3"}:
        return "REMOTE_SHARE", "NETWORK_MOUNT"
    if kind in {"ext4", "xfs", "btrfs", "tmpfs", "overlay"}:
        return "KNOWN_LOCAL", "LOCAL_MOUNT"
    return "UNKNOWN", "FILESYSTEM_UNSUPPORTED_" + kind[:80]


def classify_location(signals: LocationSignals) -> WorkspaceLocation:
    windows = signals.platform == "win32"
    key = windows_path_key(signals.path) if windows else signals.path
    reasons = list(signals.errors)
    kind: LocationKind = "UNKNOWN"
    if windows and any(
        key == root or key.startswith(root + "\\") for root in (r"\\wsl$", r"\\wsl.localhost")
    ):
        kind, reason = "UNSUPPORTED_BRIDGE", "WINDOWS_WSL_SHARE"
    elif windows and key.startswith("\\\\"):
        kind, reason = "REMOTE_SHARE", "UNC_SHARE"
    elif windows and signals.drive_type == "REMOTE":
        kind, reason = "REMOTE_SHARE", "NETWORK_DRIVE"
    elif signals.cloud == "SYNC":
        kind, reason = "KNOWN_SYNC", "CLOUD_SYNC_ROOT"
    elif any(_contains(key, root, windows) for root in signals.known_roots):
        kind, reason = "KNOWN_SYNC", "PROVIDER_SYNC_ROOT"
    elif candidates := tuple(
        root for root in signals.candidate_roots if _contains(key, root, windows)
    ):
        fingerprint = hashlib.sha256(json.dumps(sorted(candidates)).encode()).hexdigest()[:16]
        kind, reason = "UNKNOWN", "SYNC_ROOT_CANDIDATE_" + fingerprint
    elif signals.errors:
        reason = "LOCATION_PROBE_INCOMPLETE"
    elif signals.cloud_attributes:
        reason = "CLOUD_ATTRIBUTES_UNCONFIRMED"
    elif windows:
        if signals.cloud != "NOT_UNDER_SYNC_ROOT":
            reason = "CLOUD_STATUS_UNKNOWN"
        elif signals.drive_type == "REMOVABLE":
            kind, reason = "REMOVABLE", "REMOVABLE_DRIVE"
        elif signals.drive_type == "FIXED":
            kind, reason = "KNOWN_LOCAL", "FIXED_DRIVE_NO_SYNC_SIGNAL"
        else:
            reason = "DRIVE_TYPE_UNKNOWN"
    elif signals.platform.startswith("linux"):
        kind, reason = _mount_kind(key, signals.mountinfo)
    else:
        reason = "PLATFORM_PROBE_UNAVAILABLE"
    reasons.append(reason)
    return WorkspaceLocation(
        kind,
        tuple(sorted(set(reasons))),
        signals.path,
        workspace_identity(signals.path, windows=windows),
    )


def inspect_workspace_location(
    workspace: Path, *, probe: Callable[[Path], LocationSignals] | None = None
) -> WorkspaceLocation:
    return classify_location((probe or probe_workspace)(workspace))


def _confirmed(location: WorkspaceLocation) -> bool:
    path = Path(location.canonical_path) / CONFIRMATION_FILE
    try:
        with path.open("rb") as source:
            raw = source.read(65_537)
        if len(raw) > 65_536:
            return False
        value = WorkspaceLocationConfirmation.model_validate_json(raw)
        return (
            value.workspace_id == location.workspace_id
            and value.classification == location.kind
            and value.reason_digest == location.reason_digest
        )
    except (OSError, ValueError, TypeError):
        return False


def check_workspace_location(
    workspace: Path, *, probe: Callable[[Path], LocationSignals] | None = None
) -> WorkspaceLocation:
    location = inspect_workspace_location(workspace, probe=probe)
    if location.blocked:
        raise WorkspaceLocationError(location)
    if location.kind == "UNKNOWN":
        if not _confirmed(location):
            raise WorkspaceLocationError(location)
        location = replace(location, confirmed=True)
    return location


def confirm_workspace_location(
    workspace: Path,
    *,
    probe: Callable[[Path], LocationSignals] | None = None,
    expected: WorkspaceLocation | None = None,
) -> WorkspaceLocation:
    location = inspect_workspace_location(workspace, probe=probe)
    if location.blocked:
        raise WorkspaceLocationError(location)
    if location.kind != "UNKNOWN":
        return location
    if expected is not None and (location.workspace_id, location.kind, location.reason_digest) != (
        expected.workspace_id,
        expected.kind,
        expected.reason_digest,
    ):
        raise WorkspaceLocationError(location)
    root = Path(location.canonical_path)
    root.mkdir(parents=True, exist_ok=True)
    record = WorkspaceLocationConfirmation(
        workspace_id=location.workspace_id,
        reason_digest=location.reason_digest,
        confirmed_at=datetime.now(UTC),
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=".workspace-location-",
            suffix=".tmp",
            delete=False,
        ) as sink:
            temporary = Path(sink.name)
            sink.write(record.model_dump_json(indent=2))
            sink.write("\n")
        os.replace(temporary, root / CONFIRMATION_FILE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return replace(location, confirmed=True)


def forget_workspace_location(workspace: Path) -> None:
    (workspace.expanduser().resolve() / CONFIRMATION_FILE).unlink(missing_ok=True)
