import asyncio
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from tests.embedding_fixtures import sample_assets

from thoth.adapters.embeddings.asset_files import verified_bytes
from thoth.adapters.embeddings.assets import LocalModelAssets
from thoth.adapters.embeddings.manifest import granite_manifest
from thoth.domain.model_asset import AssetFile, ModelAssetError


async def test_revoked_authority_after_copy_cannot_publish(tmp_path: Path) -> None:
    source, store = sample_assets(tmp_path)
    revoked = False

    def authority() -> None:
        if revoked:
            raise ModelAssetError("LOCAL_RETRIEVAL_SCOPE_DENIED")

    def progress(_count: int) -> None:
        nonlocal revoked
        revoked = True

    with pytest.raises(ModelAssetError, match="SCOPE_DENIED"):
        await store.import_directory(source, progress=progress, check_authority=authority)
    assert not store.destination.exists()
    assert not list((store.root / ".staging").iterdir())


async def test_cancel_import_confirms_copier_exit_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from thoth.adapters.embeddings import assets

    source, store = sample_assets(tmp_path)
    entered = threading.Event()
    exited = threading.Event()
    original = assets.verified_bytes

    def slow(*args: Any, **kwargs: Any) -> bytes:
        cancelled = kwargs.get("cancelled")
        entered.set()
        assert cancelled is not None
        assert cancelled.wait(3)
        try:
            return original(*args, **kwargs)
        finally:
            exited.set()

    monkeypatch.setattr(assets, "verified_bytes", slow)
    task = asyncio.create_task(store.import_directory(source))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not store.destination.exists() and exited.is_set()
    assert not list((store.root / ".staging").iterdir())


def test_changed_open_handle_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from thoth.adapters.embeddings import asset_files

    source, store = sample_assets(tmp_path)

    def changed_handle(_fd: int) -> Path:
        return tmp_path / "outside.bin"

    monkeypatch.setattr(asset_files, "_opened_path", changed_handle)
    with pytest.raises(ModelAssetError, match="PATH_INVALID"):
        verified_bytes(source, store.manifest.files[0])


async def test_manual_import_is_verified_immutable_and_idempotent(tmp_path: Path) -> None:
    source, store = sample_assets(tmp_path)
    assert store.status().state == "MODEL_REQUIRED"
    assert (await store.import_directory(source)).state == "READY"
    first = store.destination.joinpath("model.bin").stat().st_mtime_ns
    assert store.status().state == "READY"
    assert (await store.import_directory(source)).state == "READY"
    assert store.destination.joinpath("model.bin").stat().st_mtime_ns == first
    assert list((store.root / ".staging").iterdir()) == []


@pytest.mark.parametrize("same_size", [False, True], ids=["shorter", "same-size"])
async def test_corrupt_import_never_publishes_partial_ready(
    tmp_path: Path, same_size: bool
) -> None:
    source, store = sample_assets(tmp_path)
    original = (source / "model.bin").read_bytes()
    corrupted = bytes([original[0] ^ 1]) + original[1:] if same_size else original[:-1]
    assert (len(corrupted) == len(original)) is same_size
    (source / "model.bin").write_bytes(corrupted)
    with pytest.raises(ModelAssetError, match="MODEL_HASH_MISMATCH"):
        await store.import_directory(source)
    assert not store.destination.exists()
    assert store.status().state == "MODEL_REQUIRED"
    assert (source / "model.bin").read_bytes() == corrupted


async def test_two_publishers_reuse_one_verified_destination(tmp_path: Path) -> None:
    source, first = sample_assets(tmp_path)
    second = LocalModelAssets(first.root, manifest=first.manifest)
    results = await asyncio.gather(first.import_directory(source), second.import_directory(source))
    assert all(r.state == "READY" for r in results)
    assert list((first.root / ".staging").iterdir()) == []
    assert first.status().state == "READY"


async def test_invalid_existing_destination_is_preserved(tmp_path: Path) -> None:
    source, store = sample_assets(tmp_path)
    await store.import_directory(source)
    target = store.destination / "model.bin"
    target.write_bytes(b"preserve invalid bytes for inspection")
    with pytest.raises(ModelAssetError, match="MODEL_HASH_MISMATCH"):
        await store.import_directory(source)
    assert target.read_bytes() == b"preserve invalid bytes for inspection"
    assert store.status().state == "FAILED"


def test_import_refuses_hardlinked_source(tmp_path: Path) -> None:
    source, store = sample_assets(tmp_path)
    os.link(source / "model.bin", tmp_path / "outside-link")
    with pytest.raises(ModelAssetError, match="PATH_INVALID"):
        verified_bytes(source, store.manifest.files[0])


@pytest.mark.parametrize("name", ["../model", "/model", "folder/../model", "C:/model", "a\\b"])
def test_manifest_paths_cannot_escape(name: str) -> None:
    with pytest.raises(ValueError, match="PATH_INVALID"):
        AssetFile(path=name, bytes=1, sha256="0" * 64)


def test_packaged_manifest_matches_fixed_pin_and_ten_files() -> None:
    manifest = granite_manifest()
    assert len(manifest.files) == 10
    assert manifest.total_bytes == 123586186
    assert manifest.max_tokens == 32768


async def test_long_install_path_does_not_report_ready_for_inaccessible_files(
    tmp_path: Path,
) -> None:
    source, basic = sample_assets(tmp_path)
    store = LocalModelAssets(
        tmp_path / ("long-" + "a" * 100) / ("nested-" + "b" * 100), manifest=basic.manifest
    )
    result = await store.import_directory(source)
    assert result.state == "READY"
    assert store.status().state == "READY"
    assert store.descriptor().manifest_digest == basic.manifest.digest
