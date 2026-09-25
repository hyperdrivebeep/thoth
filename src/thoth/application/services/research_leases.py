"""Short transactional lease CAS over the existing execution journal, not research truth."""

from datetime import timedelta

from thoth.application.services.request_records import RequestRecords
from thoth.domain.research_lease import ResearchLease, ResearchLeaseLost
from thoth.domain.research_request import ResearchAttempt
from thoth.ports.worker_identity import WorkerIdentityPort


class ResearchLeases:
    def __init__(self, records: RequestRecords, worker: WorkerIdentityPort) -> None:
        self.records, self.worker = records, worker

    def claim(self, attempt: ResearchAttempt) -> ResearchLease | None:
        project, thread = attempt.request_ref.project_id, str(attempt.continuation["thread_id"])
        with self.records.ledger.transaction():
            prior = self.records.journal_read(project, f"lease:{thread}", ResearchLease)
            now = self.records.clock.now()
            if prior is not None and prior.state == "HELD" and prior.expires_at > now:
                alive = self.worker.is_alive(
                    prior.process_id, prior.process_marker, prior.worker_id
                )
                if alive is not False:
                    return None
            lease = ResearchLease(
                project_id=project,
                thread_id=thread,
                operation_id=attempt.operation_id,
                request_digest=attempt.request_ref.revision_digest,
                epoch=1 if prior is None else prior.epoch + 1,
                worker_id=self.worker.worker_id,
                process_id=self.worker.process_id,
                process_marker=self.worker.process_marker,
                expires_at=now + timedelta(seconds=330),
            )
            self.records.journal(project, f"lease:{thread}", lease)
            return lease

    def validate(self, lease: ResearchLease) -> None:
        with self.records.ledger.transaction():
            current = self.records.journal_read(
                lease.project_id, f"lease:{lease.thread_id}", ResearchLease
            )
            if current is None or (current.epoch, current.worker_id, current.state) != (
                lease.epoch,
                lease.worker_id,
                "HELD",
            ):
                raise ResearchLeaseLost("ATTEMPT_LEASE_FENCED")
            # Renewal is sparse; frequent boundary reads do not create journal rows.
            if current.expires_at < self.records.clock.now() + timedelta(seconds=305):
                self.records.journal(
                    lease.project_id,
                    f"lease:{lease.thread_id}",
                    current.model_copy(
                        update={"expires_at": self.records.clock.now() + timedelta(seconds=330)}
                    ),
                )

    def release(self, lease: ResearchLease) -> None:
        with self.records.ledger.transaction():
            current = self.records.journal_read(
                lease.project_id, f"lease:{lease.thread_id}", ResearchLease
            )
            if current is not None and (current.epoch, current.worker_id) == (
                lease.epoch,
                lease.worker_id,
            ):
                self.records.journal(
                    lease.project_id,
                    f"lease:{lease.thread_id}",
                    current.model_copy(update={"state": "RELEASED"}),
                    "RELEASED",
                )
