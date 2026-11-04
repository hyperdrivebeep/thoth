"""Immutable per-user model assets with a bounded, explicit offline import."""

import asyncio
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from thoth.adapters.embeddings.asset_files import (
    check_time,
    ensure_directory,
    logical_path,
    native_path,
    plain_path,
    verified_bytes,
)
from thoth.adapters.embeddings.manifest import granite_manifest
from thoth.adapters.models.async_file_lock import async_file_lock
from thoth.domain.model_asset import (
    AssetImportBudget,
    EmbeddingAssetDescriptor,
    ModelAssetError,
    ModelAssetManifest,
    ModelAssetStatus,
)

_PENDING_COPIES: set[asyncio.Task[None]] = set()


class LocalModelAssets:
    def __init__(
        self,
        root: Path,
        *,
        manifest: ModelAssetManifest | None = None,
        budget: AssetImportBudget = AssetImportBudget(),
    ) -> None:
        self.root = logical_path(root)
        if os.name == "nt" and self.root.anchor.startswith("\\\\"):
            raise ModelAssetError("MODEL_ASSET_PATH_INVALID")
        self.manifest = manifest if manifest is not None else granite_manifest()
        self.budget = budget

    @property
    def destination(self) -> Path:
        return (
            self.root
            / self.manifest.model_id.replace("/", "--")
            / self.manifest.revision
            / self.manifest.digest
        )

    def verify(
        self,
        directory: Path,
        deadline: float | None = None,
        cancelled: threading.Event | None = None,
    ) -> None:
        for spec in self.manifest.files:
            # Only one file is retained at a time; the installer never loads an encoder.
            verified_bytes(directory, spec, deadline=deadline, cancelled=cancelled)

    def status(self, *, deadline: float | None = None) -> ModelAssetStatus:
        try:
            own = time.monotonic() + self.budget.timeout_ms / 1000
            self.verify(self.destination, min(own, deadline) if deadline is not None else own)
            state, reason = "READY", None
        except ModelAssetError as exc:
            reason = str(exc)
            state = "MODEL_REQUIRED" if reason == "MODEL_NOT_INSTALLED" else "FAILED"
        return ModelAssetStatus.model_validate(
            dict(
                state=state,
                reason_code=reason,
                manifest_digest=self.manifest.digest,
                total_bytes=self.manifest.total_bytes,
            )
        )

    def descriptor(self, *, deadline: float | None = None) -> EmbeddingAssetDescriptor:
        own = time.monotonic() + self.budget.timeout_ms / 1000
        self.verify(self.destination, min(own, deadline) if deadline is not None else own)
        return EmbeddingAssetDescriptor(
            root=str(self.destination), manifest_digest=self.manifest.digest
        )

    def _lock_file(self) -> None:
        locks = self.root / ".locks"
        ensure_directory(locks)
        plain_path(locks)
        lock = locks / (self.manifest.digest + ".lock")
        try:
            with native_path(lock).open("xb") as stream:
                stream.write(b"0")
        except FileExistsError:
            pass
        plain_path(lock)
        if native_path(lock).stat().st_nlink != 1:
            raise ModelAssetError("MODEL_ASSET_PATH_INVALID")

    def _copy(
        self,
        source: Path,
        cancelled: threading.Event,
        deadline: float,
        progress: Callable[[int], None] | None = None,
        check_authority: Callable[[], None] | None = None,
    ) -> None:
        if check_authority is not None:
            check_authority()
        if not source.is_absolute():
            raise ModelAssetError("MODEL_ASSET_PATH_INVALID")
        try:
            plain_path(source)
        except ModelAssetError as exc:
            if str(exc) == "MODEL_NOT_INSTALLED":
                raise ModelAssetError("MODEL_SOURCE_MISSING") from exc
            raise
        destination = self.destination
        if native_path(destination).exists():
            self.verify(destination, deadline, cancelled)
            return
        staging_root = self.root / ".staging"
        ensure_directory(staging_root)
        plain_path(staging_root)
        staging = staging_root / (self.manifest.digest + "-" + uuid.uuid4().hex)
        native_path(staging).mkdir()
        copied = 0
        try:
            for spec in self.manifest.files:
                if check_authority is not None:
                    check_authority()
                data = verified_bytes(source, spec, deadline=deadline, cancelled=cancelled)
                target = staging.joinpath(*spec.path.split("/"))
                ensure_directory(target.parent)
                with native_path(target).open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                del data
                copied += spec.bytes
                if progress is not None:
                    progress(copied)
            self.verify(staging, deadline, cancelled)
            ensure_directory(destination.parent)
            plain_path(destination.parent)
            check_time(deadline, cancelled)
            if check_authority is not None:
                check_authority()
            if native_path(destination).exists():
                self.verify(destination, deadline, cancelled)
                return
            # Under the digest publisher lock; READY is never replaced.
            os.rename(native_path(staging), native_path(destination))
            if os.name != "nt":
                fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError as exc:
            raise ModelAssetError("MODEL_ASSET_IO_FAILED") from exc
        finally:
            if native_path(staging).exists():
                # Only this attempt's generated child; never source or a READY installation.
                if staging.parent != staging_root or not staging.name.startswith(
                    self.manifest.digest
                ):
                    raise ModelAssetError("MODEL_ASSET_CLEANUP_UNCONFIRMED")
                shutil.rmtree(native_path(staging))

    async def import_directory(
        self,
        source: Path,
        *,
        deadline: float | None = None,
        progress: Callable[[int], None] | None = None,
        check_authority: Callable[[], None] | None = None,
    ) -> ModelAssetStatus:
        own_deadline = time.monotonic() + self.budget.timeout_ms / 1000
        deadline = min(own_deadline, deadline) if deadline is not None else own_deadline
        lock = native_path(self.root / ".locks" / (self.manifest.digest + ".lock"))
        async with async_file_lock(
            lock,
            prepare=self._lock_file,
            timeout_seconds=min(
                self.budget.lock_timeout_ms / 1000, max(0, deadline - time.monotonic())
            ),
            busy_error=lambda: ModelAssetError("MODEL_ASSET_BUSY"),
        ):
            cancelled = threading.Event()
            task = asyncio.create_task(
                asyncio.to_thread(
                    self._copy, source, cancelled, deadline, progress, check_authority
                )
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(task), max(0.001, deadline - time.monotonic())
                )
            except (asyncio.CancelledError, TimeoutError):
                cancelled.set()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task), self.budget.cleanup_timeout_ms / 1000
                    )
                except ModelAssetError:
                    pass
                except TimeoutError as exc:
                    # Keep ownership until the late copier finishes; never claim clean cancellation.
                    _PENDING_COPIES.add(task)
                    task.add_done_callback(_PENDING_COPIES.discard)
                    task.add_done_callback(
                        lambda done: None if done.cancelled() else done.exception()
                    )
                    raise ModelAssetError("MODEL_ASSET_CLEANUP_UNCONFIRMED") from exc
                raise
        return ModelAssetStatus(
            state="READY",
            manifest_digest=self.manifest.digest,
            total_bytes=self.manifest.total_bytes,
        )
