from __future__ import annotations

from typing import Protocol

from thoth.domain.export_snapshot import FrozenExportSnapshot, StagedExportBundle


class ExportBundlePort(Protocol):
    def stage(self, snapshot: FrozenExportSnapshot, snapshot_digest: str) -> StagedExportBundle: ...

    def verify(self, bundle: StagedExportBundle) -> tuple[bool, tuple[str, ...]]: ...
