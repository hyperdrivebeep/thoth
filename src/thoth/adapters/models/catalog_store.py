"""Workspace-local store for a provider's last verified model list.

Files live in `model-profiles/catalog/<provider>-<authority digest>.json`. A write goes to a
temporary file in the same folder, is read back and compared, and only then replaces the active
file; any failure leaves the active file as it was. A file that cannot be read back as the
snapshot it is named for is set aside and ignored.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from thoth.domain.model_catalog import CatalogSnapshot

_PROVIDER = re.compile(r"[a-z][a-z0-9-]{0,79}")


class CatalogStoreError(RuntimeError):
    """The snapshot could not be published; the active list is unchanged."""


class CatalogSnapshotStore:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / "model-profiles" / "catalog"

    def path(self, provider: str, authority_digest: str) -> Path:
        if (
            _PROVIDER.fullmatch(provider) is None
            or re.fullmatch(r"[0-9a-f]{64}", authority_digest) is None
        ):
            raise CatalogStoreError("CATALOG_SNAPSHOT_KEY_INVALID")
        return self.root / f"{provider}-{authority_digest}.json"

    def load(self, provider: str, authority_digest: str) -> CatalogSnapshot | None:
        path = self.path(provider, authority_digest)
        if not path.is_file():
            return None
        try:
            snapshot = CatalogSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
            if snapshot.provider != provider or snapshot.authority_digest != authority_digest:
                raise ValueError("CATALOG_SNAPSHOT_KEY_MISMATCH")
            return snapshot
        except (OSError, ValueError, ValidationError):
            self._set_aside(path)
            return None

    def save(self, snapshot: CatalogSnapshot) -> None:
        target = self.path(snapshot.provider, snapshot.authority_digest)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary.write_text(snapshot.model_dump_json(), encoding="utf-8")
            if (
                CatalogSnapshot.model_validate_json(temporary.read_text(encoding="utf-8"))
                != snapshot
            ):
                raise CatalogStoreError("CATALOG_SNAPSHOT_READBACK_MISMATCH")
            os.replace(temporary, target)
        except CatalogStoreError:
            raise
        except (OSError, ValueError, ValidationError) as exc:
            raise CatalogStoreError("CATALOG_SNAPSHOT_WRITE_FAILED") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _set_aside(path: Path) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        try:
            os.replace(path, path.with_name(f"{path.name}.corrupt-{stamp}"))
        except OSError:
            path.unlink(missing_ok=True)
