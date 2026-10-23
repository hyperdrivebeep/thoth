"""Injected location contract; no real sync roots, registry, network mounts or servers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from thoth.adapters.storage.workspace_location import (
    CONFIRMATION_FILE,
    check_workspace_location,
    classify_location,
    confirm_workspace_location,
    forget_workspace_location,
)
from thoth.adapters.storage.workspace_location_probes import (
    LocationSignals,
    cloud_status,
    probe_workspace,
    provider_roots,
)
from thoth.adapters.storage.workspace_location_registry import Hive, registered_sync_roots
from thoth.apps.workspace_paths import workspace_id, workspace_session_id
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.storage_context import storage_deployment_mode, storage_mode_scope
from thoth.domain.workspace_location import WorkspaceLocationError

CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parents[1] / "fixtures/workspace_location_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_location_classification_contract(case: dict[str, Any]) -> None:
    values = {key: value for key, value in case.items() if key not in {"name", "expected"}}
    for key in ("known_roots", "candidate_roots", "errors"):
        if key in values:
            values[key] = tuple(values[key])
    location = classify_location(LocationSignals(**values))
    assert location.kind == case["expected"]
    assert location.reasons and location.reason_digest
    assert location.confirmed is False


def test_strong_signals_and_unknown_take_precedence_over_removable() -> None:
    base = LocationSignals(r"E:\project", "win32", "REMOVABLE", "UNKNOWN")
    assert classify_location(base).kind == "UNKNOWN"
    assert classify_location(replace(base, cloud="NOT_UNDER_SYNC_ROOT")).kind == "REMOVABLE"
    assert classify_location(replace(base, cloud="SYNC")).kind == "KNOWN_SYNC"
    assert classify_location(replace(base, known_roots=("E:\\",))).kind == "KNOWN_SYNC"


def test_drivefs_configuration_is_only_a_candidate() -> None:
    signal = LocationSignals(
        r"G:\work", "win32", "FIXED", "NOT_UNDER_SYNC_ROOT", candidate_roots=("G:\\",)
    )
    assert classify_location(signal).kind == "UNKNOWN"
    assert classify_location(replace(signal, cloud="SYNC")).kind == "KNOWN_SYNC"
    assert classify_location(replace(signal, drive_type="REMOTE")).kind == "REMOTE_SHARE"


@pytest.mark.parametrize("fs", ["nfs", "nfs4", "cifs", "smb3"])
def test_network_mount_types(fs: str) -> None:
    assert (
        classify_location(
            LocationSignals("/work/x", "linux", mountinfo=f"1 0 0:1 / /work rw - {fs} x rw")
        ).kind
        == "REMOTE_SHARE"
    )


def test_mount_escaped_spaces_and_invalid_input() -> None:
    signals = LocationSignals(
        "/my workspace/x", "linux", mountinfo=r"1 0 0:1 / /my\040workspace rw - cifs x rw"
    )
    assert classify_location(signals).kind == "REMOTE_SHARE"
    assert classify_location(replace(signals, mountinfo="invalid")).kind == "UNKNOWN"


@pytest.mark.parametrize(
    "status,expected",
    [
        (0, "SYNC"),
        (0x80070186, "NOT_UNDER_SYNC_ROOT"),
        (0x80070001, "NOT_UNDER_SYNC_ROOT"),
        (0x80070001 - 2**32, "NOT_UNDER_SYNC_ROOT"),
        (0x80070178, "UNKNOWN"),
        (0x80070195, "UNKNOWN"),
        (0x80070005, "UNKNOWN"),
        (0x80070490, "UNKNOWN"),
        (0x80070002, "UNKNOWN"),
        (1, "UNKNOWN"),
    ],
)
def test_cloud_hresult_is_not_treated_as_local_on_generic_failure(
    status: int, expected: str
) -> None:
    assert cloud_status(status) == expected


def test_provider_metadata_is_injected_and_read_only() -> None:
    reads: list[Path] = []

    def read(path: Path) -> str:
        reads.append(path)
        return json.dumps({"personal": {"path": r"C:\Dropbox"}})

    roots, errors = provider_roots({"OneDrive": r"C:\OneDrive", "APPDATA": r"C:\meta"}, read)
    assert roots == (r"C:\OneDrive", r"C:\Dropbox") and not errors
    assert len(reads) == 1 and reads[0].name == "info.json"
    assert provider_roots({"APPDATA": "meta"}, lambda _: "invalid")[1]


class FakeRegistry:
    def subkeys(self, hive: Hive, path: str) -> tuple[str, ...]:
        return ("Personal", "Business1") if hive == "HKCU" else ("Provider",)

    def value(self, hive: Hive, path: str, name: str) -> object:
        assert name == "UserFolder"
        return r"C:\personal-sync" if path.endswith("Personal") else r"C:\business-sync"

    def values(self, hive: Hive, path: str) -> tuple[object, ...]:
        assert path.endswith(r"\UserSyncRoots")
        return (r"C:\registered-sync",)


def test_registry_roots_block_without_onedrive_environment() -> None:
    roots, errors = registered_sync_roots(FakeRegistry())
    assert len(roots) == 3 and not errors
    for root in roots:
        decision = classify_location(
            LocationSignals(
                root + r"\project", "win32", "FIXED", "NOT_UNDER_SYNC_ROOT", known_roots=roots
            )
        )
        assert decision.kind == "KNOWN_SYNC"


def test_registry_read_failure_never_becomes_local() -> None:
    class Denied(FakeRegistry):
        def subkeys(self, hive: Hive, path: str) -> tuple[str, ...]:
            raise PermissionError("synthetic registry denial")

    roots, errors = registered_sync_roots(Denied())
    assert not roots and errors == ("SYNC_REGISTRY_UNAVAILABLE",)
    assert (
        classify_location(
            LocationSignals(r"C:\normal", "win32", "FIXED", "NOT_UNDER_SYNC_ROOT", errors=errors)
        ).kind
        == "UNKNOWN"
    )


def test_unconfigured_registry_entries_are_not_failed_reads() -> None:
    class Unconfigured(FakeRegistry):
        def value(self, hive: Hive, path: str, name: str) -> object:
            raise FileNotFoundError("no UserFolder value")

        def values(self, hive: Hive, path: str) -> tuple[object, ...]:
            raise FileNotFoundError("no UserSyncRoots subkey")

    assert registered_sync_roots(Unconfigured()) == ((), ())


def unknown(root: Path, reason: str = "PROBE_UNAVAILABLE") -> LocationSignals:
    return LocationSignals(str(root.resolve()), "win32", errors=(reason,))


def test_confirmation_is_bound_to_path_kind_and_reason_and_forget_is_narrow(tmp_path: Path) -> None:
    root = tmp_path / "unknown"
    probe = unknown
    with pytest.raises(WorkspaceLocationError) as first:
        check_workspace_location(root, probe=probe)
    assert first.value.code == "WORKSPACE_LOCATION_UNCONFIRMED" and not root.exists()
    confirmed = confirm_workspace_location(root, probe=probe, expected=first.value.location)
    original = (root / CONFIRMATION_FILE).read_bytes()
    assert confirmed.confirmed and check_workspace_location(root, probe=probe).confirmed
    assert (root / CONFIRMATION_FILE).read_bytes() == original
    assert list(root.iterdir()) == [root / CONFIRMATION_FILE]
    with pytest.raises(WorkspaceLocationError):
        check_workspace_location(root, probe=lambda path: unknown(path, "CHANGED"))
    moved = tmp_path / "moved"
    moved.mkdir()
    (moved / CONFIRMATION_FILE).write_bytes(original)
    with pytest.raises(WorkspaceLocationError):
        check_workspace_location(moved, probe=probe)
    with pytest.raises(WorkspaceLocationError) as blocked:
        check_workspace_location(root, probe=lambda path: replace(unknown(path), cloud="SYNC"))
    assert blocked.value.code == "WORKSPACE_LOCATION_BLOCKED"
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    forget_workspace_location(root)
    assert not (root / CONFIRMATION_FILE).exists() and (root / "keep.txt").read_text() == "keep"
    assert not (root / "db").exists()


def test_changed_confirmation_basis_is_not_silently_accepted(tmp_path: Path) -> None:
    root = tmp_path / "changed"
    expected = classify_location(unknown(root))
    with pytest.raises(WorkspaceLocationError):
        confirm_workspace_location(
            root, probe=lambda path: unknown(path, "NEW_REASON"), expected=expected
        )
    assert not root.exists()


def test_blocked_location_cannot_be_confirmed_or_create_files(tmp_path: Path) -> None:
    root = tmp_path / "blocked"
    with pytest.raises(WorkspaceLocationError):
        confirm_workspace_location(root, probe=lambda path: replace(unknown(path), cloud="SYNC"))
    assert not root.exists()


def test_corrupt_confirmation_is_unconfirmed_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / CONFIRMATION_FILE
    target.write_bytes(b"bad JSON")
    with pytest.raises(WorkspaceLocationError):
        check_workspace_location(tmp_path, probe=unknown)
    assert target.read_bytes() == b"bad JSON"


def test_shared_workspace_ids_keep_the_previous_algorithms(tmp_path: Path) -> None:
    path = str(tmp_path.expanduser().resolve())
    for platform in ("win32", "linux"):
        normalized = path.casefold() if platform == "win32" else path
        assert (
            workspace_id(tmp_path, platform=platform)
            == "workspace:" + hashlib.sha256(normalized.encode()).hexdigest()[:32]
        )
    session = hashlib.sha256(os.path.normcase(path).encode()).hexdigest()[:24]
    assert workspace_session_id(tmp_path) == f"tui:workspace:{session}:local"


def test_existing_ancestor_probe_does_not_create_future_workspace(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "child"
    observed: list[Path] = []

    def windows(path: Path):
        observed.append(path)
        return "FIXED", "NOT_UNDER_SYNC_ROOT", False, False, ()

    signals = probe_workspace(
        target,
        platform="win32",
        environ={},
        windows=windows,
        drive=lambda _: "FIXED",
        drivefs=lambda _: ((), ()),
        registered=lambda: ((), ()),
    )
    assert observed == [tmp_path] and not target.exists()
    assert signals.path == str(target.resolve())


async def test_mode_context_is_task_local_and_resets_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    assert storage_deployment_mode() is DeploymentMode.LOCAL

    async def worker(mode: DeploymentMode) -> DeploymentMode:
        with storage_mode_scope(mode):
            await asyncio.sleep(0)
            assert storage_deployment_mode() is mode
            with pytest.raises(RuntimeError), storage_mode_scope(DeploymentMode.HOSTED_REVIEW):
                raise RuntimeError("synthetic")
            assert storage_deployment_mode() is mode
        return storage_deployment_mode()

    assert await asyncio.gather(
        worker(DeploymentMode.LOCAL), worker(DeploymentMode.HOSTED_REVIEW)
    ) == [DeploymentMode.LOCAL, DeploymentMode.LOCAL]
    assert storage_deployment_mode() is DeploymentMode.LOCAL


@pytest.mark.parametrize(
    "target,expected",
    [
        (r"\\server\share\workspace", "REMOTE_SHARE"),
        (r"\\?\UNC\server\share\workspace", "REMOTE_SHARE"),
        (r"\\wsl$\Ubuntu\workspace", "UNSUPPORTED_BRIDGE"),
        (r"\\?\UNC\wsl.localhost\Ubuntu\workspace", "UNSUPPORTED_BRIDGE"),
    ],
)
def test_network_aliases_are_classified_without_network_stat_or_resolve(
    target: str, expected: str
) -> None:
    def unexpected_resolve(path: Path) -> Path:
        raise AssertionError("network resolution must not run")

    signals = probe_workspace(Path(target), platform="win32", resolve=unexpected_resolve)
    assert classify_location(signals).kind == expected


def test_resolved_network_alias_is_classified_before_stat(tmp_path: Path) -> None:
    signals = probe_workspace(
        tmp_path / "alias",
        platform="win32",
        drive=lambda _: "FIXED",
        resolve=lambda _: Path(r"\\server\share\workspace"),
    )
    assert classify_location(signals).kind == "REMOTE_SHARE"


def test_resolution_failure_remains_unknown_without_creating_workspace(tmp_path: Path) -> None:
    def denied(path: Path) -> Path:
        raise PermissionError("synthetic resolution denial")

    target = tmp_path / "unreadable"
    signals = probe_workspace(target, platform="win32", drive=lambda _: "FIXED", resolve=denied)
    assert classify_location(signals).kind == "UNKNOWN"
    assert signals.errors == ("PATH_PROBE_FAILED",) and not target.exists()
