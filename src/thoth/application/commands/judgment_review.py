"""RPCs for asking the AI to re-examine one hypothesis judgment."""

from __future__ import annotations

from pydantic import Field, JsonValue

from thoth.application.services.judgment_review_service import (
    JudgmentReviewError,
    JudgmentReviewService,
)
from thoth.domain.auth import current_authenticated_actor
from thoth.domain.base import DomainModel
from thoth.domain.judgment_review import JudgmentReviewRequest, ReviewReason
from thoth.ports.conversation import ConversationDispatcherPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ReviewRequestInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    thread_id: str = Field(min_length=1, max_length=160)
    hypothesis_id: str = Field(min_length=1, max_length=160)
    hypothesis_revision_digest: str = Field(min_length=64, max_length=64)
    evidence_ref: str | None = Field(default=None, min_length=1, max_length=200)
    reason_codes: tuple[ReviewReason, ...] = Field(min_length=1, max_length=6)
    note: str = Field(default="", max_length=2_000)
    added_evidence_refs: tuple[str, ...] = Field(default=(), max_length=20)
    requested_by: str = Field(default="human:local-user", min_length=1, max_length=160)


class ReviewListInput(DomainModel):
    project_id: str = Field(min_length=1, max_length=160)
    thread_id: str | None = Field(default=None, max_length=160)
    hypothesis_id: str | None = Field(default=None, max_length=160)


class JudgmentReviewHandlers:
    def __init__(self, service: JudgmentReviewService) -> None:
        self._service = service
        self._dispatcher: ConversationDispatcherPort | None = None
        self._sending: set[str] = set()

    def bind_dispatcher(self, dispatcher: ConversationDispatcherPort) -> None:
        """The dispatcher needs the bus, which is built after the handlers are registered."""

        self._dispatcher = dispatcher

    async def request(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReviewRequestInput.model_validate(value)
        actor = current_authenticated_actor()
        try:
            record, created = self._service.open_request(
                project_id=request.project_id,
                thread_id=request.thread_id,
                hypothesis_id=request.hypothesis_id,
                hypothesis_revision_digest=request.hypothesis_revision_digest,
                evidence_ref=request.evidence_ref,
                reason_codes=request.reason_codes,
                note=request.note,
                added_evidence_refs=request.added_evidence_refs,
                requested_by=request.requested_by if actor is None else actor.actor_id,
            )
        except JudgmentReviewError as exc:
            raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, exc.code) from exc
        record, sent = await self._send(record)
        return {"request": record.model_dump(mode="json"), "created": created, "instruction": sent}

    async def list(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = ReviewListInput.model_validate(value)
        items = self._service.requests(
            request.project_id, thread_id=request.thread_id, hypothesis_id=request.hypothesis_id
        )
        return {"requests": [item.model_dump(mode="json") for item in items]}

    async def _send(self, record: JudgmentReviewRequest) -> tuple[JudgmentReviewRequest, str]:
        """Send the instruction once the request is durable. Returns the record and a send state."""

        if record.status != "OPEN":
            return record, "ALREADY_SENT"
        if record.request_id in self._sending:
            return record, "SENDING"
        self._sending.add(record.request_id)
        try:
            if self._dispatcher is None:
                outcome_ok, operation_id, status, failure = (
                    False,
                    None,
                    None,
                    "DISPATCH_UNAVAILABLE",
                )
            else:
                try:
                    outcome = await self._dispatcher.dispatch(
                        method="thread/input",
                        arguments={
                            "project_id": record.project_id,
                            "thread_id": record.thread_id,
                            "contract_version": 2,
                            "instruction": self._service.instruction_text(record),
                        },
                        idempotency_key=f"{record.request_id}:instruction:{record.attempts + 1}",
                    )
                    admission = outcome.value or {}
                    operation = admission.get("operation_id")
                    admitted_status = admission.get("status")
                    outcome_ok = outcome.success and isinstance(operation, str)
                    operation_id = operation if isinstance(operation, str) else None
                    status = admitted_status if isinstance(admitted_status, str) else None
                    failure = (
                        None if outcome_ok else (outcome.error_message or "INSTRUCTION_REJECTED")
                    )
                except Exception as exc:
                    outcome_ok, operation_id, status = False, None, None
                    failure = type(exc).__name__
        finally:
            self._sending.discard(record.request_id)
        updated = self._service.record_instruction(
            record, accepted=outcome_ok, operation_id=operation_id, status=status, failure=failure
        )
        return updated, "ACCEPTED" if outcome_ok else "FAILED"
