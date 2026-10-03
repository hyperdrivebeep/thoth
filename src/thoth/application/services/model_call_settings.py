"""A project's cut-off-call retry switch, a versioned ledger record like the memory switch."""

from __future__ import annotations

from thoth.application.services.request_records import RequestRecords
from thoth.domain.enums import EntityType
from thoth.domain.model_call_settings import (
    AUTO_RETRY_INTERRUPTED_DEFAULT,
    ProjectModelCallSettings,
)

KEY = "model-call-settings:project"


class ModelCallSettingsConflict(ValueError):
    pass


class ModelCallSettingsService:
    """Whether one model call cut off in the middle is sent again on its own."""

    def __init__(self, records: RequestRecords) -> None:
        self._records = records

    def read(self, project_id: str) -> tuple[str | None, bool]:
        record = self._records.read(project_id, EntityType.THREAD, KEY)
        if record is None:
            return None, AUTO_RETRY_INTERRUPTED_DEFAULT
        settings = ProjectModelCallSettings.model_validate(record[1])
        return record[0].revision_digest, settings.auto_retry_interrupted_model_call

    def auto_retry_interrupted(self, project_id: str) -> bool:
        return self.read(project_id)[1]

    def save(
        self, project_id: str, enabled: bool, expected_digest: str | None, actor_id: str
    ) -> str:
        with self._records.ledger.transaction():
            current, _ = self.read(project_id)
            if current != expected_digest:
                raise ModelCallSettingsConflict("MODEL_CALL_SETTINGS_REVISION_CONFLICT")
            return self._records.save(
                project_id,
                EntityType.THREAD,
                KEY,
                ProjectModelCallSettings(
                    project_id=project_id, auto_retry_interrupted_model_call=enabled
                ),
                actor_id,
            ).revision_digest
