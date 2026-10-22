"""Trusted reference-only notices, after verifying the returned research commit is readable."""

import re

from pydantic import JsonValue, ValidationError

from thoth.application.services.revision_service import CommitResult
from thoth.domain.research_execution import research_work
from thoth.domain.research_failure import FailureOrigin, ResearchFailureCause, failure_cause
from thoth.ports.ledger import LedgerPort
from thoth.protocol.deferred import current_operation
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class SavedReferenceFailure(RpcApplicationError):
    def __init__(self, reason_code: str, *, intermediate: bool) -> None:
        if re.fullmatch(r"REFERENCE_[A-Z0-9_]{1,149}", reason_code) is None:
            raise ValueError("REFERENCE_NOTICE_REASON_INVALID")
        self.reason_code = reason_code
        ko = "연구의 중간 기록은 저장됐습니다." if intermediate else "연구 결과는 저장됐습니다."
        en = (
            "Intermediate research records were saved."
            if intermediate
            else "The research result was saved."
        )
        message = (
            f"{ko} 기준(reference) 단계만 실패했습니다 (사유: {reason_code}). "
            "결과는 기록에서 볼 수 있습니다. 같은 질문을 새로 보내면 연구가 다시 실행될 수 있으니 "
            "먼저 저장된 결과를 확인하세요. / "
            f"{en} Only the reference step failed (reason: {reason_code}). "
            "You can read the result in the history. Sending the same question again as a new "
            "request may run the research again, so check the saved result first."
        )
        super().__init__(
            RpcErrorCode.DOMAIN_REJECTED,
            message,
            data={"reason_code": reason_code, "research_saved": True},
        )


def reference_failure_cause(
    error: Exception, origin: FailureOrigin
) -> tuple[ResearchFailureCause, SavedReferenceFailure | None]:
    primary = failure_cause(error, origin)
    notice = error if isinstance(error, SavedReferenceFailure) else None
    if notice is not None:
        primary = primary.model_copy(
            update={
                "reason_code": notice.reason_code,
                "exception_type": RpcApplicationError.__name__,
            }
        )
    return primary, notice


def research_commit_is_readable(
    ledger: LedgerPort, project_id: str, thread_id: str, result: dict[str, JsonValue]
) -> bool:
    operation = current_operation.get()
    if operation is None or operation.project_id != project_id:
        return False
    try:
        commit = CommitResult.model_validate(result.get("commit"))
    except ValidationError:
        return False
    if (
        commit.receipt.project_id != project_id
        or commit.receipt.recorded_at < operation.created_at
        or not commit.committed_revision_ids
        or not set(commit.committed_revision_ids).issubset(commit.receipt.subject_refs)
    ):
        return False
    work = research_work.get()
    if work is not None and (
        work.request_ref.project_id != project_id
        or work.request_ref.entity_id != f"request:{thread_id}"
        or commit.receipt.receipt_id not in work.produced_receipt_refs
        or not set(commit.committed_revision_ids).issubset(
            ref.revision_id for ref in work.produced_refs
        )
    ):
        return False
    if commit.receipt not in ledger.read_receipts(project_id):
        return False
    for identifier in commit.committed_revision_ids:
        revision = ledger.read_revision_by_id(project_id, identifier)
        if revision is None or revision.project_id != project_id:
            return False
        snapshot = ledger.read_snapshot(revision.snapshot_id)
        if snapshot is None or (
            snapshot.project_id,
            snapshot.entity_type,
            snapshot.entity_id,
        ) != (project_id, revision.entity_type, revision.entity_id):
            return False
    return True


def reference_failure(
    ledger: LedgerPort,
    project_id: str,
    thread_id: str,
    result: dict[str, JsonValue],
    reason_code: str,
) -> RpcApplicationError:
    if re.fullmatch(
        r"REFERENCE_[A-Z0-9_]{1,149}", reason_code
    ) is not None and research_commit_is_readable(ledger, project_id, thread_id, result):
        return SavedReferenceFailure(reason_code, intermediate=research_work.get() is not None)
    return RpcApplicationError(
        RpcErrorCode.DOMAIN_REJECTED, reason_code, data={"reason_code": reason_code}
    )
