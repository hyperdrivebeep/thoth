"""A project's memory switch, kept as a versioned ledger record like the model preference."""

from __future__ import annotations

from thoth.application.services.request_records import RequestRecords
from thoth.domain.enums import EntityType
from thoth.domain.memory_settings import ProjectMemorySettings

KEY = "memory-settings:project"


class MemorySettingsConflict(ValueError):
    pass


class MemorySettingsService:
    """Whether a project gives recalled memory to an investigation; on until turned off.

    Turning it off changes nothing that is stored, listed or corrected. It only stops the
    recalled memory from being placed in front of an investigation.
    """

    def __init__(self, records: RequestRecords) -> None:
        self._records = records

    def read(self, project_id: str) -> tuple[str | None, bool]:
        record = self._records.read(project_id, EntityType.THREAD, KEY)
        if record is None:
            return None, True
        return record[0].revision_digest, ProjectMemorySettings.model_validate(
            record[1]
        ).memory_injection

    def enabled(self, project_id: str) -> bool:
        return self.read(project_id)[1]

    def save(
        self, project_id: str, enabled: bool, expected_digest: str | None, actor_id: str
    ) -> str:
        with self._records.ledger.transaction():
            current, _ = self.read(project_id)
            if current != expected_digest:
                raise MemorySettingsConflict("MEMORY_SETTINGS_REVISION_CONFLICT")
            return self._records.save(
                project_id,
                EntityType.THREAD,
                KEY,
                ProjectMemorySettings(project_id=project_id, memory_injection=enabled),
                actor_id,
            ).revision_digest
