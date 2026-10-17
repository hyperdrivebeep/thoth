"""Prepare ordered lineage references; the caller retains the DAG write boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from thoth.domain.receipt_dag import ReceiptNodeKind

if TYPE_CHECKING:
    from thoth.application.workflows.thread_cycle import ThreadCycleResult
    from thoth.domain.evidence import EvidenceSpan
    from thoth.domain.research_execution import ResearchWork


def cycle_lineage_values(
    result: ThreadCycleResult,
    selected_evidence: tuple[EvidenceSpan, ...],
    r2_closed_loop_projection: dict[str, JsonValue] | None,
    work: ResearchWork | None,
) -> dict[ReceiptNodeKind, tuple[str, ...]]:
    full_memory = result.full_memory
    assert full_memory is not None
    dag_values: dict[ReceiptNodeKind, tuple[str, ...]] = {
        ReceiptNodeKind.EVIDENCE: tuple(span.span_id for span in selected_evidence),
        ReceiptNodeKind.MODEL: result.model_ids,
        ReceiptNodeKind.ACTION: (result.action_plan.plan_id,),
        ReceiptNodeKind.REVISION: (result.commit.receipt.receipt_id,),
        ReceiptNodeKind.MEMORY: tuple(
            item.memory_revision_id
            for item in (
                *full_memory.committed,
                *full_memory.revised,
                *full_memory.held,
                *full_memory.quarantined,
            )
        ),
    }
    if work is not None:
        dag_values[ReceiptNodeKind.REVISION] = (
            *dag_values[ReceiptNodeKind.REVISION],
            work.request_ref.revision_digest,
            *(ref.revision_digest for ref in work.record_refs),
        )
    if result.outcome is not None:
        dag_values[ReceiptNodeKind.OUTCOME] = (result.outcome.outcome_id,)
    if r2_closed_loop_projection is not None:
        compiled = r2_closed_loop_projection.get("compiled_spec")
        sandbox_receipt = r2_closed_loop_projection.get("sandbox_receipt")
        outcome_value = r2_closed_loop_projection.get("outcome")
        if isinstance(compiled, dict):
            dag_values[ReceiptNodeKind.EXECUTION] = (str(compiled.get("attempt_id")),)
        if isinstance(sandbox_receipt, dict):
            dag_values[ReceiptNodeKind.SANDBOX] = (str(sandbox_receipt.get("receipt_digest")),)
        if isinstance(outcome_value, dict):
            dag_values[ReceiptNodeKind.OUTCOME] = (str(outcome_value.get("outcome_id")),)
    return dag_values
