"""Pinning cannot manufacture ownership and protects a live reference from release."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from thoth.adapters.storage.workspace_owner import (
    acquire_workspace_owner,
    holds_workspace,
    pin_workspace_owner,
)


def test_missing_workspace_is_not_created_or_owned(tmp_path: Path) -> None:
    workspace = tmp_path / "absent"
    assert not holds_workspace(workspace)
    with pin_workspace_owner(workspace) as held:
        assert not held and not workspace.exists()
    assert not workspace.exists()


def test_pin_keeps_existing_lock_across_original_release(tmp_path: Path) -> None:
    ownership = acquire_workspace_owner(tmp_path)
    with pin_workspace_owner(tmp_path) as held:
        assert held and holds_workspace(tmp_path)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(ownership.release).result(timeout=5)
        assert holds_workspace(tmp_path)
        with pin_workspace_owner(tmp_path) as nested:
            assert nested
        assert holds_workspace(tmp_path)
    assert not holds_workspace(tmp_path)


def test_failed_body_releases_only_the_pin(tmp_path: Path) -> None:
    ownership = acquire_workspace_owner(tmp_path)
    try:
        try:
            with pin_workspace_owner(tmp_path) as held:
                assert held
                raise RuntimeError("injected")
        except RuntimeError:
            pass
        assert holds_workspace(tmp_path)
    finally:
        ownership.release()
    assert not holds_workspace(tmp_path)
