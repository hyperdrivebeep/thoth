from pydantic import AwareDatetime

from thoth.domain.base import DomainModel


class ResearchLease(DomainModel):
    project_id: str
    thread_id: str
    operation_id: str
    request_digest: str
    epoch: int
    worker_id: str
    process_id: int
    process_marker: str
    expires_at: AwareDatetime
    state: str = "HELD"


class ResearchLeaseLost(RuntimeError):
    pass


class ResearchPaused(RuntimeError):
    pass
