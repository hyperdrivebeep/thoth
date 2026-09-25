from __future__ import annotations

from typing import Protocol

from thoth.domain.control_record import ControlRecord


class ControlRecordStorePort(Protocol):
    def append(self, value: ControlRecord) -> None: ...
    def read(self, project_id: str, namespace: str, record_id: str) -> ControlRecord | None: ...
    def read_digest(self, project_id: str, digest: str) -> ControlRecord | None: ...
    def list(
        self,
        project_id: str,
        namespace: str,
        record_type: str | None = None,
        *,
        latest_only: bool = True,
    ) -> tuple[ControlRecord, ...]: ...
