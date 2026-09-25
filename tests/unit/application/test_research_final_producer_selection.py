"""An explicit final save can resolve a multi-revision producer without hiding provenance."""

from types import SimpleNamespace
from typing import cast

import pytest

from thoth.application.services.research_basis_capture import (
    capture_record_producers,
    result_basis,
    select_final_record_producer,
)
from thoth.domain.enums import EntityType
from thoth.domain.research_execution import ResearchBoundary, ResearchWork
from thoth.domain.research_reference import RevisionRef
from thoth.domain.research_request import ThreadRequestRevision
from thoth.ports.ledger import LedgerPort

PROJECT = "project:selection"
KEY = "THREAD:learning:selection"


def _ref(ordinal: int, *, project: str = PROJECT) -> RevisionRef:
    return RevisionRef(
        project_id=project,
        entity_type="THREAD",
        entity_id="learning:selection",
        revision_id=f"revision:{ordinal}",
        revision_digest=str(ordinal) * 64,
    )


def _work() -> ResearchWork:
    return ResearchWork(
        request_ref=RevisionRef(
            project_id=PROJECT,
            entity_type="THREAD",
            entity_id="request:selection",
            revision_id="revision:request",
            revision_digest="a" * 64,
        ),
        effective_question="Controlled selection",
        boundary=cast(ResearchBoundary, object()),
    )


class _Ledger:
    def __init__(self, refs: tuple[RevisionRef, ...], *, head: str | None = None) -> None:
        self.refs = {ref.revision_digest: ref for ref in refs}
        self.head = refs[-1].revision_digest if head is None else head
        self.missing_receipt: set[str] = set()
        self.unchanged_receipt: set[str] = set()
        self.changed_identity: set[str] = set()

    def read_revision_by_digest(self, project: str, digest: str) -> SimpleNamespace | None:
        ref = self.refs.get(digest)
        if ref is None or project != PROJECT:
            return None
        return SimpleNamespace(
            entity_type=EntityType.THREAD,
            entity_id="other" if digest in self.changed_identity else ref.entity_id,
            revision_id=ref.revision_id,
        )

    def read_receipts(self, project: str) -> tuple[SimpleNamespace, ...]:
        assert project == PROJECT
        return tuple(
            SimpleNamespace(
                receipt_id=f"receipt:{ref.revision_id}",
                subject_refs=(ref.revision_id,),
                before_head_set_digest="b" * 64,
                after_head_set_digest=(
                    "b" * 64 if ref.revision_digest in self.unchanged_receipt else "c" * 64
                ),
            )
            for ref in self.refs.values()
            if ref.revision_digest not in self.missing_receipt
        )

    def read_heads(self, project: str) -> dict[str, str]:
        assert project == PROJECT
        return {KEY: self.head}


def _basis(work: ResearchWork, ledger: _Ledger):
    request = SimpleNamespace(
        project_id=PROJECT,
        policy_digest="f" * 64,
        cutoff_at="2026-09-05T00:00:00+00:00",
    )
    return result_basis(
        work, cast(ThreadRequestRevision, request), cast(LedgerPort, ledger)
    )


def test_explicit_final_ref_preserves_pending_ref_and_receipts() -> None:
    pending, final = _ref(1), _ref(2)
    work, ledger = _work(), _Ledger((pending, final))
    work.record_refs.extend((pending, final))
    select_final_record_producer(work, final)
    capture_record_producers(work, cast(LedgerPort, ledger))
    basis = _basis(work, ledger)
    assert basis.coverage == "COMPLETE"
    assert basis.reasons == ()
    assert basis.produced_refs == (pending, final)
    assert len(basis.producer_receipt_refs) == 2
    assert basis.produced_final_heads == {KEY: final.revision_digest}
    assert work.record_refs == [pending, final]


def test_multiple_producers_without_explicit_selection_remain_unknown() -> None:
    pending, final = _ref(1), _ref(2)
    work, ledger = _work(), _Ledger((pending, final))
    work.record_refs.extend((pending, final))
    capture_record_producers(work, cast(LedgerPort, ledger))
    basis = _basis(work, ledger)
    assert basis.coverage == "PARTIAL"
    assert "BASIS_FINAL_PRODUCER_UNRESOLVED" in basis.reasons
    assert KEY not in basis.produced_final_heads
    assert len(basis.producer_receipt_refs) == 2


@pytest.mark.parametrize(
    "defect", ["missing_receipt", "unchanged_receipt", "wrong_identity", "changed_head"]
)
def test_invalid_final_selection_never_becomes_current(defect: str) -> None:
    pending, final = _ref(1), _ref(2)
    work, ledger = _work(), _Ledger((pending, final))
    work.record_refs.extend((pending, final))
    select_final_record_producer(work, final)
    if defect == "missing_receipt":
        ledger.missing_receipt.add(final.revision_digest)
    elif defect == "unchanged_receipt":
        ledger.unchanged_receipt.add(final.revision_digest)
    elif defect == "wrong_identity":
        ledger.changed_identity.add(final.revision_digest)
    else:
        ledger.head = "9" * 64
    capture_record_producers(work, cast(LedgerPort, ledger))
    basis = _basis(work, ledger)
    assert basis.coverage == "PARTIAL"
    assert basis.reasons


@pytest.mark.parametrize("foreign", [False, True])
def test_foreign_or_unrecorded_nomination_does_not_resolve_ambiguity(foreign: bool) -> None:
    pending, final = _ref(1), _ref(2)
    work, ledger = _work(), _Ledger((pending, final))
    work.record_refs.extend((pending, final))
    candidate = _ref(2, project="project:foreign") if foreign else _ref(3)
    select_final_record_producer(work, candidate)
    capture_record_producers(work, cast(LedgerPort, ledger))
    basis = _basis(work, ledger)
    assert basis.coverage == "PARTIAL"
    assert "BASIS_FINAL_PRODUCER_UNRESOLVED" in basis.reasons


def test_prior_unknown_reason_is_preserved_after_valid_selection() -> None:
    pending, final = _ref(1), _ref(2)
    work, ledger = _work(), _Ledger((pending, final))
    work.record_refs.extend((pending, final))
    work.basis_reasons.append("LEGACY_BASIS_UNKNOWN")
    select_final_record_producer(work, final)
    capture_record_producers(work, cast(LedgerPort, ledger))
    basis = _basis(work, ledger)
    assert basis.coverage == "PARTIAL"
    assert basis.reasons == ("LEGACY_BASIS_UNKNOWN",)
    assert basis.produced_final_heads == {KEY: final.revision_digest}
