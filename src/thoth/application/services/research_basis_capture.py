"""Capture actual reads and successful cycle publications in the current attempt."""

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import JsonValue

if TYPE_CHECKING:
    from thoth.application.workflows.thread_cycle import ThreadCycleResult

from typing import cast

from thoth.domain.memory import FullMemoryRevision
from thoth.domain.model import ContextPack
from thoth.domain.research_basis import ResearchResultBasis, SourceBasisRef
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_reference import RevisionRef
from thoth.domain.research_request import ThreadRequestRevision
from thoth.domain.revision import StagedRevision
from thoth.ports.ledger import LedgerPort


def capture_consumed(project: str, key: str, digest: str) -> None:
    work = research_work.get()
    if work is not None and work.request_ref.project_id == project:
        prior = work.consumed_heads.get(key)
        if prior is not None and prior != digest and work.produced_final_heads.get(key) != digest:
            work.basis_reasons.append("BASIS_CONSUMER_CHANGED_DURING_REQUEST")
        work.consumed_heads.setdefault(key, digest)


def capture_memory_context(
    project: str, memories: tuple[FullMemoryRevision, ...], ledger: LedgerPort
) -> None:
    work = research_work.get()
    if work is None or work.request_ref.project_id != project:
        return
    for memory in memories:
        if memory.revision_digest not in work.memory_revision_refs:
            work.memory_revision_refs.append(memory.revision_digest)
        owner = ledger.read_revision_by_digest(project, memory.owner_revision_ref)
        if owner is None:
            work.basis_reasons.append("BASIS_MEMORY_OWNER_UNAVAILABLE")
        else:
            capture_consumed(
                project, f"{owner.entity_type.value}:{owner.entity_id}", owner.revision_digest
            )


def capture_rendered_memory(context: ContextPack, ledger: LedgerPort) -> None:
    """Observe only memory actually present in an invocation's final ContextPack."""
    work = research_work.get()
    if work is None or work.request_ref.project_id != context.project_id:
        return
    raw = context.research_context.get("authorized_project_memory")
    if isinstance(raw, dict):
        included = cast(dict[str, object], raw).get("included", ())
        if isinstance(included, (tuple, list)):
            capture_memory_context(
                context.project_id,
                tuple(
                    FullMemoryRevision.model_validate(item)
                    for item in cast(tuple[object, ...] | list[object], included)
                ),
                ledger,
            )
    refs = context.research_context.get("analysis_memory_refs", ())
    if isinstance(refs, (tuple, list)):
        for value in cast(tuple[object, ...] | list[object], refs):
            if not isinstance(value, dict):
                raise ValueError("BASIS_MEMORY_REFERENCE_INVALID")
            ref = cast(dict[str, object], value)
            if ref.get("project_id") != context.project_id:
                raise ValueError("BASIS_MEMORY_PROJECT_MISMATCH")
            owner = ledger.read_revision_by_digest(
                context.project_id, str(ref["owner_revision_ref"])
            )
            if owner is None:
                raise ValueError("BASIS_MEMORY_OWNER_UNAVAILABLE")
            digest = str(ref["revision_digest"])
            if digest not in work.memory_revision_refs:
                work.memory_revision_refs.append(digest)
            capture_consumed(
                context.project_id,
                f"{owner.entity_type.value}:{owner.entity_id}",
                owner.revision_digest,
            )


def render_analysis_memory(
    work: ResearchWork | None, memories: tuple[FullMemoryRevision, ...]
) -> str:
    if work is not None:
        work.context["analysis_memory_refs"] = [
            {
                "project_id": item.project_id,
                "revision_digest": item.revision_digest,
                "owner_revision_ref": item.owner_revision_ref,
            }
            for item in memories
        ]
    if not memories:
        return ""
    return "\n\nAuthorized project memory references:\n" + "\n".join(
        f"[{item.memory_revision_id}] {item.content_excerpt}" for item in memories
    )


def capture_record_producers(work: ResearchWork, ledger: LedgerPort) -> None:
    """The manifest's explicit record_refs select committed records, not latest heads."""
    receipts = ledger.read_receipts(work.request_ref.project_id)
    by_key: dict[str, set[str]] = {}
    for ref in work.record_refs:
        if ref.project_id != work.request_ref.project_id:
            raise ValueError("BASIS_PRODUCER_PROJECT_MISMATCH")
        revision = ledger.read_revision_by_digest(ref.project_id, ref.revision_digest)
        if revision is None or (
            revision.entity_type.value,
            revision.entity_id,
            revision.revision_id,
        ) != (ref.entity_type, ref.entity_id, ref.revision_id):
            work.basis_reasons.append("BASIS_RECORD_PRODUCER_UNRESOLVED")
            continue
        receipt = next(
            (
                receipt
                for receipt in receipts
                if ref.revision_id in receipt.subject_refs
                and receipt.before_head_set_digest != receipt.after_head_set_digest
            ),
            None,
        )
        if receipt is None:
            work.basis_reasons.append("BASIS_RECORD_PRODUCER_UNRESOLVED")
            continue
        if ref not in work.produced_refs:
            work.produced_refs.append(ref)
        if receipt.receipt_id not in work.produced_receipt_refs:
            work.produced_receipt_refs.append(receipt.receipt_id)
        by_key.setdefault(f"{ref.entity_type}:{ref.entity_id}", set()).add(ref.revision_digest)
    for key, digests in by_key.items():
        selected = work.produced_final_heads.get(key)
        if selected is None and len(digests) == 1:
            work.produced_final_heads[key] = next(iter(digests))
        elif selected is None or selected not in digests:
            work.produced_final_heads.pop(key, None)
            work.basis_reasons.append("BASIS_FINAL_PRODUCER_UNRESOLVED")


def select_final_record_producer(work: ResearchWork, ref: RevisionRef) -> None:
    """Nominate only the exact ref returned by this work's final record save."""
    if ref.project_id != work.request_ref.project_id or ref not in work.record_refs:
        work.basis_reasons.append("BASIS_FINAL_PRODUCER_UNRESOLVED")
        return
    work.produced_final_heads[f"{ref.entity_type}:{ref.entity_id}"] = ref.revision_digest


def capture_cycle_produced(staged: tuple[StagedRevision, ...], receipt_ref: str) -> None:
    work = research_work.get()
    if work is None:
        return
    for item in staged:
        revision = item.revision
        if revision.project_id != work.request_ref.project_id:
            raise ValueError("BASIS_PRODUCER_PROJECT_MISMATCH")
        ref = RevisionRef(
            project_id=revision.project_id,
            entity_type=revision.entity_type.value,
            entity_id=revision.entity_id,
            revision_id=revision.revision_id,
            revision_digest=revision.revision_digest,
            schema_version=item.snapshot.schema_version,
        )
        work.produced_refs.append(ref)
    work.produced_receipt_refs.append(receipt_ref)


def select_result_producers(
    work: ResearchWork, revision_ids: tuple[str, ...], receipt_id: str
) -> None:
    """Bind the actual returned cycle's commit, rather than the latest observed writer."""
    if receipt_id not in work.produced_receipt_refs:
        work.basis_reasons.append("BASIS_FINAL_PRODUCER_UNRESOLVED")
        return
    selected = [ref for ref in work.produced_refs if ref.revision_id in revision_ids]
    expected = {f"{ref.entity_type}:{ref.entity_id}": ref.revision_digest for ref in selected}
    if len(selected) != len(revision_ids) or len(expected) != len(selected):
        work.basis_reasons.append("BASIS_FINAL_PRODUCER_UNRESOLVED")
        return
    work.produced_final_heads = expected


def cycle_result_payload(
    result: "ThreadCycleResult", work: ResearchWork | None
) -> dict[str, JsonValue]:
    if work is not None:
        select_result_producers(
            work, result.commit.committed_revision_ids, result.commit.receipt.receipt_id
        )
    return result.model_dump(mode="json")


def result_basis(
    work: ResearchWork, request: ThreadRequestRevision, ledger: LedgerPort
) -> ResearchResultBasis:
    reasons = list(work.basis_reasons)
    if work.produced_refs and not work.produced_final_heads:
        reasons.append("BASIS_FINAL_PRODUCER_UNRESOLVED")
    heads = ledger.read_heads(request.project_id)
    receipts = {receipt.receipt_id: receipt for receipt in ledger.read_receipts(request.project_id)}
    committed_ids = {
        ref
        for receipt_id in work.produced_receipt_refs
        if receipt_id in receipts
        for ref in receipts[receipt_id].subject_refs
    }
    for ref in work.produced_refs:
        if ref.revision_id not in committed_ids:
            reasons.append("BASIS_FINAL_PRODUCER_UNRESOLVED")
    if any(heads.get(key) != digest for key, digest in work.produced_final_heads.items()):
        reasons.append("BASIS_PRODUCER_HEAD_CHANGED")
    return ResearchResultBasis(
        request_ref=work.request_ref,
        consumed_heads=work.consumed_heads,
        produced_refs=tuple(work.produced_refs),
        produced_final_heads=work.produced_final_heads,
        producer_receipt_refs=tuple(work.produced_receipt_refs),
        source_basis=tuple(
            SourceBasisRef(
                artifact_id=span.artifact_id,
                source_version_id=span.source_version_id,
                span_id=span.span_id,
                text_sha256=span.text_sha256,
            )
            for span in {
                **work.consumed_sources,
                **{span.span_id: span for span in work.evidence},
            }.values()
        ),
        memory_revision_refs=tuple(work.memory_revision_refs),
        policy_digest=request.policy_digest,
        cutoff_at=datetime.fromisoformat(request.cutoff_at),
        coverage="PARTIAL" if reasons else "COMPLETE",
        reasons=tuple(sorted(set(reasons))),
    )
