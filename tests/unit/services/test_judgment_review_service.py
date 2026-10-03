"""Review requests: one open request per hypothesis revision, and three honest outcomes."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

import pytest
from pydantic import JsonValue

from thoth.application.services.control_record_service import ControlRecordService
from thoth.application.services.judgment_review_service import (
    JudgmentReviewError,
    JudgmentReviewService,
)
from thoth.domain.control_record import ControlRecord
from thoth.domain.enums import OperationState
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.domain.judgment_review import outcome_for, relation_text
from thoth.domain.operation import OperationRecord
from thoth.domain.project import WorkThread

NOW = datetime(2026, 9, 30, tzinfo=UTC)
DIGEST = "a" * 64
NEXT_DIGEST = "b" * 64


class Clock:
    def now(self) -> datetime:
        return NOW


class Ids:
    def __init__(self) -> None:
        self._n = count(1)

    def new(self, prefix: str) -> str:
        return f"{prefix}:{next(self._n)}"


class Controls:
    def __init__(self) -> None:
        self.rows: list[ControlRecord] = []

    def append(self, value: ControlRecord) -> None:
        self.rows.append(value)

    def read(self, project_id: str, namespace: str, record_id: str) -> ControlRecord | None:
        rows = [
            r
            for r in self.rows
            if (r.project_id, r.namespace, r.record_id) == (project_id, namespace, record_id)
        ]
        return max(rows, key=lambda r: r.version, default=None)

    def read_digest(self, project_id: str, digest: str) -> ControlRecord | None:
        return next((r for r in self.rows if r.record_digest == digest), None)

    def list(
        self,
        project_id: str,
        namespace: str,
        record_type: str | None = None,
        *,
        latest_only: bool = True,
    ) -> tuple[ControlRecord, ...]:
        ids = {
            r.record_id
            for r in self.rows
            if r.project_id == project_id and r.namespace == namespace
        }
        found = [self.read(project_id, namespace, item) for item in sorted(ids)]
        return tuple(
            r
            for r in found
            if r is not None and (record_type is None or r.record_type == record_type)
        )


def hypothesis(digest: str = DIGEST, *, support: int = 2, counter: int = 0) -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis_revision_id="revision:h1",
        hypothesis_id="hypothesis:1",
        project_id="p",
        object_id="object:1",
        portfolio_id="portfolio:1",
        statement="조건 alpha에서 지연이 늘어난다",
        observed_problem="지연",
        primary_intent=None,
        evidence_basis="EVIDENCE",
        scope={},
        evidence_refs=tuple(f"span:{i}" for i in range(support)),
        counterevidence_refs=tuple(f"span:c{i}" for i in range(counter)),
        revision_digest=digest,
        created_at=NOW,
    )


class Hypotheses:
    def __init__(self) -> None:
        self.head = hypothesis()

    def read_hypothesis(
        self, project_id: str, hypothesis_id: str, revision_digest: str | None
    ) -> HypothesisRecord | None:
        if hypothesis_id != self.head.hypothesis_id:
            return None
        return self.head


class Threads:
    def read(self, thread_id: str) -> WorkThread | None:
        if thread_id != "thread:1":
            return None
        return WorkThread(
            thread_id="thread:1",
            project_id="p",
            cycle_id="cycle:1",
            problem="q",
            working_head_digest=DIGEST,
        )


class Operations:
    def __init__(self) -> None:
        self.states: dict[str, OperationRecord] = {}

    def put(
        self, operation_id: str, state: OperationState, result: dict[str, JsonValue] | None = None
    ) -> None:
        self.states[operation_id] = OperationRecord(
            operation_id=operation_id,
            project_id="p",
            method="thread/input",
            idempotency_key=operation_id,
            scope_digest=DIGEST,
            state=state,
            result=result,
            created_at=NOW,
        )

    def read(self, operation_id: str) -> OperationRecord | None:
        return self.states.get(operation_id)


def build() -> tuple[JudgmentReviewService, Hypotheses, Controls, Operations]:
    controls, hypotheses, operations = Controls(), Hypotheses(), Operations()
    ids = Ids()
    service = JudgmentReviewService(
        controls=ControlRecordService(store=controls, clock=Clock(), ids=ids),  # type: ignore[arg-type]
        store=controls,  # type: ignore[arg-type]
        hypotheses=hypotheses,  # type: ignore[arg-type]
        threads=Threads(),  # type: ignore[arg-type]
        operations=operations,  # type: ignore[arg-type]
        clock=Clock(),
        ids=ids,
    )
    return service, hypotheses, controls, operations


def request(service: JudgmentReviewService, digest: str = DIGEST, evidence_ref: str | None = None):
    return service.open_request(
        project_id="p",
        thread_id="thread:1",
        hypothesis_id="hypothesis:1",
        hypothesis_revision_digest=digest,
        evidence_ref=evidence_ref,
        reason_codes=("EVIDENCE_INTERPRETATION",),
        note="  다시 봐 주세요 ",
        added_evidence_refs=(),
        requested_by="human:local-user",
    )


def test_second_request_for_the_same_revision_returns_the_open_one() -> None:
    service, _, controls, _ = build()
    first, created = request(service)
    again, created_again = request(service)
    assert created and not created_again
    assert again.request_id == first.request_id and first.status == "OPEN"
    assert len({row.record_id for row in controls.rows}) == 1
    assert (
        first.note == "다시 봐 주세요"
        and first.previous_relation == "SUPPORT:2|COUNTER:0|APPRAISAL:UNASSESSED"
    )


def test_a_request_alone_never_changes_the_hypothesis_or_its_relation() -> None:
    service, hypotheses, _, _ = build()
    before = hypotheses.head
    item, _ = request(service, evidence_ref="span:0")
    assert hypotheses.head is before and item.previous_relation == "SUPPORT"
    assert relation_text(before, "span:zzz") == "UNASSESSED"
    assert relation_text(hypothesis(counter=1), "span:c0") == "COUNTER"


def test_stale_revision_thread_or_hypothesis_is_rejected_without_a_record() -> None:
    service, _, controls, _ = build()
    with pytest.raises(JudgmentReviewError, match="REVISION_CHANGED"):
        request(service, digest=NEXT_DIGEST)
    with pytest.raises(JudgmentReviewError, match="THREAD_NOT_FOUND"):
        service.open_request(
            project_id="p",
            thread_id="thread:x",
            hypothesis_id="hypothesis:1",
            hypothesis_revision_digest=DIGEST,
            evidence_ref=None,
            reason_codes=("OTHER",),
            note="",
            added_evidence_refs=(),
            requested_by="u",
        )
    with pytest.raises(JudgmentReviewError, match="HYPOTHESIS_NOT_FOUND"):
        service.open_request(
            project_id="p",
            thread_id="thread:1",
            hypothesis_id="hypothesis:x",
            hypothesis_revision_digest=DIGEST,
            evidence_ref=None,
            reason_codes=("OTHER",),
            note="",
            added_evidence_refs=(),
            requested_by="u",
        )
    assert controls.rows == []


def test_failed_send_leaves_the_request_open_and_it_can_be_sent_again() -> None:
    service, _, _, _ = build()
    item, _ = request(service)
    failed = service.record_instruction(item, accepted=False, failure="MODEL_UNAVAILABLE")
    assert (
        failed.status,
        failed.instruction_state,
        failed.instruction_failure,
        failed.attempts,
    ) == ("OPEN", "FAILED", "MODEL_UNAVAILABLE", 1)
    again, created = request(service)
    assert (
        not created
        and again.request_id == item.request_id
        and again.instruction_failure == "MODEL_UNAVAILABLE"
    )
    sent = service.record_instruction(
        again, accepted=True, operation_id="operation:1", status="QUEUED_AFTER_CURRENT"
    )
    assert (sent.status, sent.instruction_state, sent.attempts, sent.instruction_failure) == (
        "REVIEWING",
        "ACCEPTED",
        2,
        None,
    )


@pytest.mark.parametrize(
    ("result", "head", "outcome"),
    [
        (
            {
                "answer_status": "ANSWERED",
                "portfolio": {"hypotheses": [{"hypothesis_id": "hypothesis:1"}]},
            },
            hypothesis(NEXT_DIGEST),
            "UPHELD",
        ),
        (
            {
                "answer_status": "ANSWERED",
                "portfolio": {"hypotheses": [{"hypothesis_id": "hypothesis:1"}]},
            },
            hypothesis(NEXT_DIGEST, support=1, counter=1),
            "CHANGED",
        ),
        ({"answer_status": "ANSWERED", "portfolio": {"hypotheses": []}}, hypothesis(), "CHANGED"),
        (
            {
                "answer_status": "PARTIAL_HOLD",
                "portfolio": {"hypotheses": [{"hypothesis_id": "hypothesis:1"}]},
            },
            hypothesis(NEXT_DIGEST),
            "HOLD",
        ),
    ],
)
def test_resolution_is_upheld_changed_or_hold_only_after_the_instruction_runs(
    result: dict[str, object], head: HypothesisRecord, outcome: str
) -> None:
    service, hypotheses, _, _ = build()
    item, _ = request(service)
    service.record_instruction(
        item, accepted=True, operation_id="operation:9", status="ACCEPTED_RUNNING"
    )
    hypotheses.head = head
    assert service.resolve_for_operation("p", "thread:1", "operation:other", result) == ()
    (resolved,) = service.resolve_for_operation(
        "p", "thread:1", "operation:9", result, result_revision_digest="c" * 64
    )
    assert resolved.status == "RESOLVED" and resolved.resolution is not None
    assert resolved.resolution.outcome == outcome
    assert resolved.resolution.previous_relation != "" and resolved.resolution_ref is not None
    (listed,) = service.requests("p")
    assert (
        listed.status == "RESOLVED"
        and listed.resolution is not None
        and listed.resolution.outcome == outcome
    )
    # once resolved, the current revision (new or unchanged) may be reviewed again
    _, created = request(service, digest=hypotheses.head.revision_digest)
    assert created
    assert (
        outcome_for(previous="x", resulting="x", answer_status=None, in_result=True)[0] == "UPHELD"
    )


@pytest.mark.parametrize(
    "result",
    [
        {"answer_status": "ANSWERED"},
        {"answer_status": "ANSWERED", "portfolio": None},
        {"answer_status": "ANSWERED", "portfolio": {}},
        {"answer_status": "ANSWERED", "portfolio": {"hypotheses": None}},
    ],
)
def test_a_result_without_a_hypothesis_list_does_not_resolve_and_the_request_can_be_sent_again(
    result: dict[str, object],
) -> None:
    service, hypotheses, _, _ = build()
    item, _ = request(service)
    service.record_instruction(
        item, accepted=True, operation_id="operation:9", status="ACCEPTED_RUNNING"
    )
    hypotheses.head = hypothesis(NEXT_DIGEST)
    (reopened,) = service.resolve_for_operation("p", "thread:1", "operation:9", result)
    assert reopened.status == "OPEN" and reopened.resolution is None
    assert reopened.instruction_state == "FAILED" and reopened.instruction_operation_id is None
    assert reopened.instruction_failure == "RESULT_HAS_NO_HYPOTHESIS_LIST"
    (listed,) = service.requests("p")
    assert listed.status == "OPEN" and listed.resolution is None
    again = service.record_instruction(
        listed, accepted=True, operation_id="operation:10", status="ACCEPTED_RUNNING"
    )
    assert again.status == "REVIEWING"


def test_reading_shows_a_finished_or_failed_instruction_without_writing() -> None:
    service, _, controls, operations = build()
    item, _ = request(service)
    service.record_instruction(
        item, accepted=True, operation_id="operation:7", status="ACCEPTED_RUNNING"
    )
    written = len(controls.rows)
    operations.put("operation:7", OperationState.FAILED)
    (shown,) = service.requests("p")
    assert (shown.status, shown.instruction_state) == (
        "OPEN",
        "FAILED",
    ) and shown.instruction_failure == "OPERATION_FAILED"
    assert len(controls.rows) == written
    # the next request persists the reopened state and reuses the same request
    again, created = request(service)
    assert not created and again.request_id == item.request_id and again.status == "OPEN"
    assert len(controls.rows) == written + 1
