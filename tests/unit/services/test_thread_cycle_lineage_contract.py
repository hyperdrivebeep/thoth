"""Observe the root writer's ordered values using partial typed projection fixtures."""

from typing import cast
from unittest.mock import Mock

import pytest
from pydantic import JsonValue

from thoth.application.commands.thread_analysis import (
    ThreadAnalysisCommandHandlers,
    ThreadInputRequest,
)
from thoth.application.services.receipt_dag_service import ReceiptDagService
from thoth.application.services.revision_service import CommitResult
from thoth.application.workflows.thread_cycle import ThreadCycleResult
from thoth.domain.action import ActionPlan
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.memory import FullMemoryRevision
from thoth.domain.memory_preparation import FullMemoryPromotionResult
from thoth.domain.outcome import OutcomeRecord
from thoth.domain.receipt import Receipt
from thoth.domain.receipt_dag import ReceiptNodeKind as Kind
from thoth.domain.research_execution import ResearchBoundary, ResearchWork
from thoth.domain.research_reference import RevisionRef


class Harness(ThreadAnalysisCommandHandlers):
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[tuple[Kind, tuple[str, ...]]], Receipt | None]] = []
        writer = Mock(spec=ReceiptDagService)

        def record(
            project: str, values: dict[Kind, tuple[str, ...]], receipt: Receipt | None
        ) -> None:
            self.calls.append((project, list(values.items()), receipt))

        writer.record_cycle.side_effect = record
        self._receipt_dag = cast(ReceiptDagService, writer)

    def record(
        self,
        result: ThreadCycleResult,
        evidence: tuple[EvidenceSpan, ...],
        r2: dict[str, JsonValue] | None,
        work: ResearchWork | None,
    ) -> None:
        self._record_cycle_lineage(
            ThreadInputRequest(project_id="p", thread_id="t"), result, evidence, r2, work
        )


def ref(digest: str) -> RevisionRef:
    return RevisionRef(
        project_id="p",
        entity_type="THREAD",
        entity_id="t",
        revision_id=digest,
        revision_digest=digest,
    )


@pytest.mark.parametrize("has_work", [False, True])
@pytest.mark.parametrize("has_outcome", [False, True])
@pytest.mark.parametrize("r2_kind", ["none", "complete", "non_dict", "missing_keys"])
def test_ordered_lineage_values_at_actual_root_writer(
    has_work: bool,
    has_outcome: bool,
    r2_kind: str,
) -> None:
    # Only fields read by this pure mapping are populated. Normal RPC tests cover valid full models.
    def memory(name: str) -> FullMemoryRevision:
        return FullMemoryRevision.model_construct(memory_revision_id=name)

    receipt = Receipt.model_construct(receipt_id="receipt:cycle")
    result = ThreadCycleResult.model_construct(
        full_memory=FullMemoryPromotionResult.model_construct(
            committed=(memory("c"), memory("duplicate")),
            revised=(memory("r"), memory("duplicate")),
            held=(memory("h"),),
            quarantined=(memory("q"),),
        ),
        model_ids=("model:2", "model:1", "model:2"),
        action_plan=ActionPlan.model_construct(plan_id="plan"),
        commit=CommitResult.model_construct(receipt=receipt),
        outcome=OutcomeRecord.model_construct(outcome_id="out:cycle") if has_outcome else None,
    )
    evidence = tuple(EvidenceSpan.model_construct(span_id=name) for name in ("e:2", "e:1", "e:2"))
    work = (
        ResearchWork(
            request_ref=ref("request"),
            effective_question="fixture",
            boundary=cast(ResearchBoundary, Mock(spec=ResearchBoundary)),
            record_refs=[ref("record:2"), ref("record:1"), ref("record:2")],
        )
        if has_work
        else None
    )
    cases: dict[str, dict[str, JsonValue] | None] = {
        "none": None,
        "complete": {
            "compiled_spec": {"attempt_id": "execution:r2"},
            "sandbox_receipt": {"receipt_digest": "sandbox:r2"},
            "outcome": {"outcome_id": "out:r2"},
        },
        "non_dict": {"compiled_spec": [], "sandbox_receipt": "ignored", "outcome": 0},
        "missing_keys": {"compiled_spec": {}, "sandbox_receipt": {}, "outcome": {}},
    }
    h = Harness()
    h.record(result, evidence, cases[r2_kind], work)
    revision = (
        ("receipt:cycle", "request", "record:2", "record:1", "record:2")
        if has_work
        else ("receipt:cycle",)
    )
    expected: list[tuple[Kind, tuple[str, ...]]] = [
        (Kind.EVIDENCE, ("e:2", "e:1", "e:2")),
        (Kind.MODEL, ("model:2", "model:1", "model:2")),
        (Kind.ACTION, ("plan",)),
        (Kind.REVISION, revision),
        (Kind.MEMORY, ("c", "duplicate", "r", "duplicate", "h", "q")),
    ]
    output = {"complete": "out:r2", "missing_keys": "None"}.get(r2_kind, "out:cycle")
    if has_outcome:
        expected.append((Kind.OUTCOME, (output,)))
    if r2_kind in {"complete", "missing_keys"}:
        expected.extend(
            [
                (Kind.EXECUTION, ("execution:r2" if r2_kind == "complete" else "None",)),
                (Kind.SANDBOX, ("sandbox:r2" if r2_kind == "complete" else "None",)),
            ]
        )
        if not has_outcome:
            expected.append((Kind.OUTCOME, (output,)))
    assert len(h.calls) == 1
    assert h.calls[0][0] == "p" and h.calls[0][1] == expected
    assert h.calls[0][2] is receipt


def test_missing_full_memory_asserts_before_other_fields_or_writer() -> None:
    h = Harness()
    with pytest.raises(AssertionError) as error:
        h.record(ThreadCycleResult.model_construct(full_memory=None), (), None, None)
    assert error.value.args == () and h.calls == []
