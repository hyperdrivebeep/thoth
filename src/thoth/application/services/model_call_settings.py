"""A project's model-call switches (cut-off retry, hypothesis contract v3) as a ledger record."""

from __future__ import annotations

from thoth.application.services.request_records import RequestRecords
from thoth.domain.enums import EntityType
from thoth.domain.model_call_settings import (
    AUTO_RETRY_INTERRUPTED_DEFAULT,
    HYPOTHESIS_CONTRACT_V3_DEFAULT,
    ProjectModelCallSettings,
)

KEY = "model-call-settings:project"


class ModelCallSettingsConflict(ValueError):
    pass


class ModelCallSettingsService:
    """Whether one model call cut off in the middle is sent again on its own."""

    def __init__(self, records: RequestRecords) -> None:
        self._records = records

    def snapshot(self, project_id: str) -> tuple[str | None, bool, bool]:
        """The settings digest and both switches as they stand."""
        record = self._records.read(project_id, EntityType.THREAD, KEY)
        if record is None:
            return None, AUTO_RETRY_INTERRUPTED_DEFAULT, HYPOTHESIS_CONTRACT_V3_DEFAULT
        settings = ProjectModelCallSettings.model_validate(record[1])
        return (
            record[0].revision_digest,
            settings.auto_retry_interrupted_model_call,
            settings.hypothesis_contract_v3,
        )

    def read(self, project_id: str) -> tuple[str | None, bool]:
        digest, retry, _ = self.snapshot(project_id)
        return digest, retry

    def hypothesis_contract_v3(self, project_id: str) -> bool:
        return self.snapshot(project_id)[2]

    def auto_retry_interrupted(self, project_id: str) -> bool:
        return self.read(project_id)[1]

    def save(
        self,
        project_id: str,
        enabled: bool | None,
        expected_digest: str | None,
        actor_id: str,
        hypothesis_v3: bool | None = None,
    ) -> str:
        """Save the switches; one left as None keeps its value."""
        with self._records.ledger.transaction():
            current, retry, contract = self.snapshot(project_id)
            if current != expected_digest:
                raise ModelCallSettingsConflict("MODEL_CALL_SETTINGS_REVISION_CONFLICT")
            return self._records.save(
                project_id,
                EntityType.THREAD,
                KEY,
                ProjectModelCallSettings(
                    project_id=project_id,
                    auto_retry_interrupted_model_call=retry if enabled is None else enabled,
                    hypothesis_contract_v3=contract if hypothesis_v3 is None else hypothesis_v3,
                ),
                actor_id,
            ).revision_digest
