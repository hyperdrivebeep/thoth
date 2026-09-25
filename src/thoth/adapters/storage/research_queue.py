"""Versioned queue records in the existing transactional control ledger."""

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.research_queue import QueuedResearchInput
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.research_queue import ResearchQueueStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

QUEUE_NAMESPACE = "RESEARCH_EXECUTION"


class ControlResearchQueueStore(ResearchQueueStorePort):
    def __init__(
        self,
        controls: ControlRecordStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self.controls = controls
        self.clock = clock
        self.ids = ids

    def read(self, project_id: str, operation_id: str) -> QueuedResearchInput | None:
        record = self.controls.read(project_id, QUEUE_NAMESPACE, f"queue:{operation_id}")
        if record is None:
            return None
        if record.record_type != "QueuedResearchInput":
            raise ValueError("QUEUE_RECORD_TYPE_MISMATCH")
        item = QueuedResearchInput.model_validate(record.payload)
        if item.project_id != project_id or item.operation_id != operation_id:
            raise ValueError("QUEUE_RECORD_IDENTITY_MISMATCH")
        return item

    def list(self, project_id: str, thread_id: str) -> tuple[QueuedResearchInput, ...]:
        return tuple(
            item for item in self.list_project(project_id) if item.thread_id == thread_id
        )

    def list_project(self, project_id: str) -> tuple[QueuedResearchInput, ...]:
        records = self.controls.list(
            project_id, QUEUE_NAMESPACE, "QueuedResearchInput", latest_only=True
        )
        items = (
            QueuedResearchInput.model_validate(record.payload)
            for record in records
            if record.record_id.startswith("queue:")
        )
        return tuple(
            sorted(
                items,
                key=lambda item: (item.thread_id, item.ordinal, item.operation_id),
            )
        )

    def save(self, item: QueuedResearchInput) -> None:
        record_id = f"queue:{item.operation_id}"
        current = self.controls.read(item.project_id, QUEUE_NAMESPACE, record_id)
        draft: dict[str, object] = {
            "control_revision_id": self.ids.new("control-revision"),
            "project_id": item.project_id,
            "namespace": QUEUE_NAMESPACE,
            "record_type": "QueuedResearchInput",
            "record_id": record_id,
            "version": 1 if current is None else current.version + 1,
            "state": item.state,
            "payload": item.model_dump(mode="python"),
            "supersedes_digest": None if current is None else current.record_digest,
            "created_at": self.clock.now(),
        }
        self.controls.append(
            ControlRecord.model_validate(
                {
                    **draft,
                    "record_digest": domain_digest(
                        "RESEARCH_EXECUTION_QueuedResearchInput",
                        "1.0.0",
                        canonical_payload(draft),
                    ),
                }
            )
        )
