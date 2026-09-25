"""Rebuild an observation summary from its exact committed run, never repeat evaluation."""

from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.improvement import RecursiveImprovementResult
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.ledger import LedgerPort


class ImprovementObservationResults:
    def __init__(
        self, records: ControlRecordStorePort, controls: ControlRecordService, ledger: LedgerPort
    ) -> None:
        self.records, self.controls, self.ledger = records, controls, ledger

    @staticmethod
    def verify(record: ControlRecord, payload: dict[str, object] | None = None) -> None:
        body = record.model_dump(
            mode="python", exclude={"record_digest", "canonical_truth", "schema_version"}
        )
        if payload is not None:
            body["payload"] = payload
        if record.record_digest != domain_digest(
            f"{record.namespace}_{record.record_type}", "1.0.0", canonical_payload(body)
        ):
            raise ValueError("IMPROVEMENT_OBSERVATION_RECORD_INVALID")

    def read(self, project: str, thread: str, event: str) -> RecursiveImprovementResult | None:
        cached = self.records.read(project, "IMPROVEMENT_RUNTIME", f"observation-result:{event}")
        if cached is not None:
            self.verify(cached)
            result = RecursiveImprovementResult.model_validate(cached.payload["result"])
            if (
                result.project_id != project
                or result.thread_id != thread
                or cached.state != result.state.value
            ):
                raise ValueError("IMPROVEMENT_OBSERVATION_BINDING_INVALID")
            return result
        failure = self.records.read(project, "IMPROVEMENT_RUNTIME", f"failure:{event}")
        if failure is None:
            return None
        self.verify(failure)
        if failure.payload.get("thread_id") != thread:
            raise ValueError("IMPROVEMENT_FAILURE_EVENT_BINDING_INVALID")
        matches: list[RecursiveImprovementResult] = []
        for record in self.records.list(project, "IMPROVEMENT_RUNTIME", "RUN"):
            if (
                record.payload.get("thread_id"),
                record.payload.get("failure_fingerprint"),
                record.payload.get("failure_count"),
            ) != (
                thread,
                failure.payload.get("failure_fingerprint"),
                failure.payload.get("ordinal"),
            ):
                continue
            result = RecursiveImprovementResult.model_validate(record.payload)
            self.verify(record, result.model_dump(mode="python"))
            if record.record_id != result.run_id or record.state != result.state.value:
                raise ValueError("IMPROVEMENT_RUN_BINDING_INVALID")
            matches.append(result)
        if len(matches) > 1:
            raise ValueError("IMPROVEMENT_OBSERVATION_RESULT_AMBIGUOUS")
        if not matches:
            return None
        self.save(event, matches[0])
        return matches[0]

    def save(self, event: str, result: RecursiveImprovementResult) -> None:
        with self.ledger.transaction():
            previous = self.records.read(
                result.project_id, "IMPROVEMENT_RUNTIME", f"observation-result:{event}"
            )
            if (
                previous is not None
                and RecursiveImprovementResult.model_validate(previous.payload["result"]) == result
            ):
                self.verify(previous)
                return
            self.controls.create(
                project_id=result.project_id,
                namespace="IMPROVEMENT_RUNTIME",
                record_type="OBSERVATION_RESULT",
                record_id=f"observation-result:{event}",
                state=result.state.value,
                payload={"result": result.model_dump(mode="json")},
            )
