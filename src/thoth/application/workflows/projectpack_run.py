from __future__ import annotations

from thoth.application.workflows.thread_cycle import ThreadCycleResult
from thoth.domain.base import DomainModel
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.project import Project, WorkThread


class ProjectPackRunResult(DomainModel):
    pack_id: str
    project: Project
    thread: WorkThread
    artifact_ids: tuple[str, ...]
    evidence: tuple[EvidenceSpan, ...]
    evidence_count: int
    selected_evidence: tuple[EvidenceSpan, ...]
    selected_evidence_count: int
    scripted_model: bool
    cycle: ThreadCycleResult
