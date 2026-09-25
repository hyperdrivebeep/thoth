from __future__ import annotations

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class ControlRecordService:
    def __init__(
        self,
        *,
        store: ControlRecordStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    def read(self, project_id: str, namespace: str, record_id: str) -> ControlRecord | None:
        return self._store.read(project_id, namespace, record_id)

    def read_digest(self, project_id: str, digest: str) -> ControlRecord | None:
        return self._store.read_digest(project_id, digest)

    def create(
        self,
        *,
        project_id: str,
        namespace: str,
        record_type: str,
        state: str,
        payload: dict[str, object],
        record_id: str | None = None,
    ) -> ControlRecord:
        record = self.stage(
            project_id=project_id,
            namespace=namespace,
            record_type=record_type,
            state=state,
            payload=payload,
            record_id=record_id,
        )
        self._store.append(record)
        return record

    def stage(
        self,
        *,
        project_id: str,
        namespace: str,
        record_type: str,
        state: str,
        payload: dict[str, object],
        record_id: str | None = None,
    ) -> ControlRecord:
        effective_id = record_id or self._ids.new(f"{namespace.lower()}-{record_type.lower()}")
        current = self._store.read(project_id, namespace, effective_id)
        draft: dict[str, object] = {
            "control_revision_id": self._ids.new("control-revision"),
            "project_id": project_id,
            "namespace": namespace,
            "record_type": record_type,
            "record_id": effective_id,
            "version": 1 if current is None else current.version + 1,
            "state": state,
            "payload": payload,
            "supersedes_digest": None if current is None else current.record_digest,
            "created_at": self._clock.now(),
        }
        record = ControlRecord.model_validate(
            {
                **draft,
                "record_digest": domain_digest(
                    f"{namespace}_{record_type}",
                    "1.0.0",
                    canonical_payload(draft),
                ),
            }
        )
        return record
