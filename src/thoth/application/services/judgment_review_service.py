"""Record, send and resolve requests to re-examine a hypothesis judgment.

Requests live in the shared append-only control records. Sending the instruction to the research
loop is a separate step after the request is durable, so a failed send leaves an OPEN request that
can be sent again. Resolution reads the outcome of the operation that carried the instruction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from thoth.application.services.control_record_service import ControlRecordService
from thoth.domain.control_record import ControlRecord
from thoth.domain.judgment_review import (
    JUDGMENT_REVIEW_NAMESPACE,
    JUDGMENT_REVIEW_RECORD_TYPE,
    OPEN_STATUSES,
    REVIEW_REASON_LABELS,
    JudgmentReviewRequest,
    JudgmentReviewResolution,
    ReviewReason,
    outcome_for,
    relation_text,
)
from thoth.ports.control_record import ControlRecordStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.operation import OperationStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.thread import ThreadStorePort


class JudgmentReviewError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class JudgmentReviewService:
    def __init__(
        self,
        *,
        controls: ControlRecordService,
        store: ControlRecordStorePort,
        hypotheses: HypothesisStorePort,
        threads: ThreadStorePort,
        operations: OperationStorePort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._controls, self._store, self._ids = controls, store, ids
        self._hypotheses, self._threads = hypotheses, threads
        self._operations, self._clock = operations, clock

    def open_request(
        self,
        *,
        project_id: str,
        thread_id: str,
        hypothesis_id: str,
        hypothesis_revision_digest: str,
        evidence_ref: str | None,
        reason_codes: tuple[ReviewReason, ...],
        note: str,
        added_evidence_refs: tuple[str, ...],
        requested_by: str,
    ) -> tuple[JudgmentReviewRequest, bool]:
        """Return the open request for this hypothesis revision, creating it only when none is.

        Nothing here awaits, so two concurrent calls cannot both create a request.
        """

        thread = self._threads.read(thread_id)
        if thread is None or thread.project_id != project_id:
            raise JudgmentReviewError("REVIEW_THREAD_NOT_FOUND")
        head = self._hypotheses.read_hypothesis(project_id, hypothesis_id, None)
        if head is None:
            raise JudgmentReviewError("REVIEW_HYPOTHESIS_NOT_FOUND")
        if head.revision_digest != hypothesis_revision_digest:
            raise JudgmentReviewError("REVIEW_HYPOTHESIS_REVISION_CHANGED")
        for candidate in self._all(project_id):
            if (
                candidate.hypothesis_id == hypothesis_id
                and candidate.hypothesis_revision_digest == hypothesis_revision_digest
            ):
                existing = self._settle(candidate, persist=True)
                if existing.status in OPEN_STATUSES:
                    return existing, False
        request = JudgmentReviewRequest(
            request_id=self._ids.new("judgment-review"),
            project_id=project_id,
            thread_id=thread_id,
            hypothesis_id=hypothesis_id,
            hypothesis_revision_digest=hypothesis_revision_digest,
            evidence_ref=evidence_ref,
            reason_codes=reason_codes,
            note=note.strip(),
            added_evidence_refs=added_evidence_refs,
            requested_by=requested_by,
            previous_relation=relation_text(head, evidence_ref),
            created_at=self._clock.now(),
        )
        self._write(request)
        return request, True

    def instruction_text(self, request: JudgmentReviewRequest) -> str:
        record = self._hypotheses.read_hypothesis(
            request.project_id, request.hypothesis_id, request.hypothesis_revision_digest
        )
        reasons = ", ".join(REVIEW_REASON_LABELS[item] for item in request.reason_codes)
        lines = [
            "재검토 요청: 다음 가설의 판단을 근거를 다시 살펴 재검토하세요.",
            f"가설: {'' if record is None else record.statement} ({request.hypothesis_id})",
            f"대상 근거: {request.evidence_ref or '가설 전체'}",
            f"요청 이유: {reasons}",
        ]
        if request.note:
            lines.append(f"메모: {request.note}")
        if request.added_evidence_refs:
            lines.append("함께 봐야 할 자료: " + ", ".join(request.added_evidence_refs))
        lines.append(
            "관계 값은 이 요청만으로 바꾸지 말고, "
            "근거를 다시 판단해 유지·변경·보류 중 하나로 답하세요."
        )
        return "\n".join(lines)

    def record_instruction(
        self,
        request: JudgmentReviewRequest,
        *,
        accepted: bool,
        operation_id: str | None = None,
        status: str | None = None,
        failure: str | None = None,
    ) -> JudgmentReviewRequest:
        if request.status != "OPEN":
            return request
        updated = request.model_copy(
            update={
                "attempts": request.attempts + 1,
                "instruction_state": "ACCEPTED" if accepted else "FAILED",
                "instruction_operation_id": operation_id if accepted else None,
                "instruction_status": status if accepted else None,
                "instruction_failure": None if accepted else (failure or "INSTRUCTION_REJECTED"),
                "status": "REVIEWING" if accepted else "OPEN",
            }
        )
        self._write(updated)
        return updated

    def requests(
        self,
        project_id: str,
        *,
        thread_id: str | None = None,
        hypothesis_id: str | None = None,
    ) -> tuple[JudgmentReviewRequest, ...]:
        """Latest version of each request, after settling any whose instruction has finished."""

        # A read never writes: unsettled requests are shown as they would be once settled.
        settled = tuple(self._settle(item, persist=False) for item in self._all(project_id))
        return tuple(
            item
            for item in settled
            if (thread_id is None or item.thread_id == thread_id)
            and (hypothesis_id is None or item.hypothesis_id == hypothesis_id)
        )

    def resolve_for_operation(
        self,
        project_id: str,
        thread_id: str,
        operation_id: str,
        result: Mapping[str, object],
        *,
        result_revision_digest: str | None = None,
    ) -> tuple[JudgmentReviewRequest, ...]:
        resolved: list[JudgmentReviewRequest] = []
        for item in self._all(project_id):
            if (
                item.status == "REVIEWING"
                and item.thread_id == thread_id
                and item.instruction_operation_id == operation_id
            ):
                resolved.append(self._resolve(item, result, result_revision_digest, persist=True))
        return tuple(resolved)

    def _settle(self, item: JudgmentReviewRequest, *, persist: bool) -> JudgmentReviewRequest:
        if item.status != "REVIEWING" or item.instruction_operation_id is None:
            return item
        operation = self._operations.read(item.instruction_operation_id)
        if operation is None:
            return item
        if operation.state.value in {"FAILED", "CANCELLED"}:
            reopened = item.model_copy(
                update={
                    "status": "OPEN",
                    "instruction_state": "FAILED",
                    "instruction_operation_id": None,
                    "instruction_failure": f"OPERATION_{operation.state.value}",
                }
            )
            if persist:
                self._write(reopened)
            return reopened
        if operation.state.value == "SUCCEEDED" and operation.result is not None:
            return self._resolve(item, operation.result, None, persist=persist)
        return item

    def _resolve(
        self,
        item: JudgmentReviewRequest,
        result: Mapping[str, object],
        result_revision_digest: str | None,
        *,
        persist: bool,
    ) -> JudgmentReviewRequest:
        listed = _result_hypothesis_ids(result)
        if listed is None:
            # A result without a hypothesis list says nothing about this hypothesis: ask again.
            reopened = item.model_copy(
                update={
                    "status": "OPEN",
                    "instruction_state": "FAILED",
                    "instruction_operation_id": None,
                    "instruction_failure": "RESULT_HAS_NO_HYPOTHESIS_LIST",
                }
            )
            if persist:
                self._write(reopened)
            return reopened
        head = self._hypotheses.read_hypothesis(item.project_id, item.hypothesis_id, None)
        in_result = item.hypothesis_id in listed
        resulting = relation_text(head if in_result else None, item.evidence_ref)
        status = result.get("answer_status")
        outcome, rationale = outcome_for(
            previous=item.previous_relation,
            resulting=resulting,
            answer_status=status if isinstance(status, str) else None,
            in_result=in_result,
        )
        resolved = item.model_copy(
            update={
                "status": "RESOLVED",
                "resolution": JudgmentReviewResolution(
                    outcome=outcome,
                    previous_relation=item.previous_relation,
                    resulting_relation=resulting,
                    rationale=rationale,
                    evidence_refs=()
                    if head is None or not in_result
                    else (*head.evidence_refs, *head.counterevidence_refs),
                    result_revision_digest=result_revision_digest,
                    resolved_at=self._clock.now(),
                ),
            }
        )
        current = self._store.read(item.project_id, JUDGMENT_REVIEW_NAMESPACE, item.request_id)
        version = 1 if current is None else current.version + 1
        resolved = resolved.model_copy(update={"resolution_ref": f"{item.request_id}#v{version}"})
        if persist:
            self._write(resolved)
        return resolved

    def _all(self, project_id: str) -> tuple[JudgmentReviewRequest, ...]:
        rows = self._store.list(
            project_id, JUDGMENT_REVIEW_NAMESPACE, JUDGMENT_REVIEW_RECORD_TYPE, latest_only=True
        )
        return tuple(
            sorted(
                (JudgmentReviewRequest.model_validate(row.payload) for row in rows),
                key=lambda item: item.created_at,
            )
        )

    def _write(self, request: JudgmentReviewRequest) -> ControlRecord:
        return self._controls.create(
            project_id=request.project_id,
            namespace=JUDGMENT_REVIEW_NAMESPACE,
            record_type=JUDGMENT_REVIEW_RECORD_TYPE,
            state=request.status,
            payload=request.model_dump(mode="json"),
            record_id=request.request_id,
        )


def _result_hypothesis_ids(result: Mapping[str, object]) -> frozenset[str] | None:
    """Ids in the result's hypothesis list, or None when the result carries no such list."""

    portfolio = result.get("portfolio")
    if not isinstance(portfolio, Mapping):
        return None
    hypotheses = cast(Mapping[str, object], portfolio).get("hypotheses")
    if not isinstance(hypotheses, list):
        return None
    ids: set[str] = set()
    for item in cast(list[object], hypotheses):
        identifier = (
            cast(Mapping[str, object], item).get("hypothesis_id")
            if isinstance(item, Mapping)
            else None
        )
        if isinstance(identifier, str):
            ids.add(identifier)
    return frozenset(ids)
