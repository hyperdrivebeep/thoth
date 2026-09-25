import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import cast

import pytest
from pydantic import ValidationError

from thoth.application.services.selected_evidence_reader import SelectedEvidenceReader
from thoth.domain.artifact import ArtifactEnvelope, SourceLocator, StructuralDocument
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.ids import ProjectId, SourceVersionId
from thoth.domain.research_history import HistoricalResultInput
from thoth.domain.research_request import CurrentResultManifest, RevisionRef
from thoth.domain.resource_scope import ResourceScopeError
from thoth.domain.restore import RestoreError
from thoth.domain.source_time import (
    SourceTimeAssessment,
    SourceTimeAssessmentMode,
    SourceTimeMutationBasis,
    SourceTimeReasonCode,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _strings(value: object) -> list[str]:
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in cast(list[object], value))
    return cast(list[str], value)


class RecordingArtifactLedger(ArtifactLedgerPort):
    def __init__(self, reader: Callable[[str], EvidenceSpan | None]) -> None:
        self._reader = reader

    def read_evidence(self, span_id: str) -> EvidenceSpan | None:
        return self._reader(span_id)

    def persist_ingestion(
        self,
        document: StructuralDocument,
        source_version_id: SourceVersionId,
        evidence_candidates: tuple[EvidenceSpan, ...],
    ) -> None:
        raise AssertionError("unexpected ingestion")

    def read_artifact(self, artifact_id: str) -> ArtifactEnvelope | None:
        raise AssertionError("unexpected artifact read")

    def list_source_versions(self, project_id: str, artifact_id: str) -> tuple[str, ...]:
        raise AssertionError("unexpected version list")

    def read_structure(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> StructuralDocument | None:
        raise AssertionError("unexpected structure read")

    def read_structure_metadata_digest(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> str | None:
        raise AssertionError("unexpected structure metadata read")

    def list_artifacts(self, project_id: ProjectId) -> tuple[ArtifactEnvelope, ...]:
        raise AssertionError("unexpected artifact list")

    def list_evidence(self, project_id: ProjectId) -> tuple[EvidenceSpan, ...]:
        raise AssertionError("unexpected evidence list")

    def update_evidence(self, span: EvidenceSpan) -> None:
        raise AssertionError("unexpected evidence update")

    def update_artifact_cutoff(self, expected: ArtifactEnvelope, state: CutoffState) -> None:
        raise AssertionError("unexpected cutoff update")

    def read_source_time(
        self, project_id: str, artifact_id: str, source_version_id: str
    ) -> SourceTimeAssessment | None:
        raise AssertionError("unexpected source-time read")

    def list_source_times(self, project_id: str) -> tuple[SourceTimeAssessment, ...]:
        raise AssertionError("unexpected source-time list")

    def apply_source_time(
        self,
        basis: SourceTimeMutationBasis,
        *,
        next_state: CutoffState,
        mode: SourceTimeAssessmentMode,
        reason_code: SourceTimeReasonCode,
        assessed_at: datetime,
        require_unknown: bool,
        allow_resolved: bool,
    ) -> SourceTimeAssessment:
        raise AssertionError("unexpected source-time mutation")


def fixture(
    count: int = 149,
) -> tuple[HistoricalResultInput, dict[str, object], tuple[EvidenceSpan, ...]]:
    spans = tuple(
        EvidenceSpan(
            span_id=f"span:{i}",
            project_id="p",
            artifact_id="artifact:one",
            source_version_id="source-version:one",
            locator=SourceLocator(page=1, line=i + 1),
            exact_text=(text := f"Original selected measurement {i}"),
            text_sha256=hashlib.sha256(text.encode()).hexdigest(),
            extraction_method="fixture:1",
            support_state=SupportState.EXTRACTED,
            authority_state=AuthorityState.INFORMAL,
            verification_state=VerificationState.SCHEMA_VALID,
            cutoff_state=CutoffState.ELIGIBLE,
        )
        for i in range(count)
    )
    request = HistoricalResultInput(
        project_id="p",
        thread_id="thread:one",
        request_revision_digest="a" * 64,
        result_revision_digest="b" * 64,
        include_selected_evidence=True,
    )
    manifest = CurrentResultManifest(
        request_ref=RevisionRef(
            project_id="p",
            entity_type="THREAD",
            entity_id="request:thread:one",
            revision_id="revision:one",
            revision_digest="a" * 64,
        ),
        operation_id="operation:one",
        attempt_epoch=1,
        basis_digest="c" * 64,
        phase="HOLD",
        input_ids=(),
        source_refs=tuple(s.span_id for s in spans),
        source_basis={s.span_id: f"{s.source_version_id}:{s.text_sha256}" for s in spans},
        result={"answer": "A partial answer without portfolio or action plan"},
    )
    return request, _record(json.loads(manifest.model_dump_json())), spans


def test_manifest_order_paging_only_reads_the_requested_ids() -> None:
    request, manifest, spans = fixture()
    by_id = {s.span_id: s for s in spans}
    reads: list[str] = []

    def read(identifier: str) -> EvidenceSpan | None:
        reads.append(identifier)
        return by_id.get(identifier)

    reader = SelectedEvidenceReader(RecordingArtifactLedger(read))
    pages = [
        reader.read(request.model_copy(update={"selected_evidence_offset": offset}), manifest)
        for offset in (0, 50, 100)
    ]
    assert [len(p.items) for p in pages] == [50, 50, 49]
    assert [p.next_offset for p in pages] == [50, 100, None]
    assert reads == _strings(manifest["source_refs"])
    assert {p.actor_scope_digest for p in pages} == {pages[0].actor_scope_digest}
    assert all(p.selected_count == 149 and p.result_revision_digest == "b" * 64 for p in pages)
    assert all(i.availability == "AVAILABLE" and i.span is not None for p in pages for i in p.items)


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "version",
        "hash",
        "text",
        "unknown-basis",
        "bad-basis",
        "foreign",
        "wrong-id",
        "denied",
    ],
)
def test_unavailable_or_mismatched_sources_never_expose_bytes(failure: str) -> None:
    request, manifest, spans = fixture(1)
    span = spans[0]
    if failure == "missing":
        span = None
    elif failure == "version":
        span = span.model_copy(update={"source_version_id": "source-version:new"})
    elif failure == "hash":
        span = span.model_copy(update={"text_sha256": "0" * 64})
    elif failure == "text":
        span = span.model_copy(update={"exact_text": "changed without updating digest"})
    elif failure == "foreign":
        span = span.model_copy(update={"project_id": "foreign"})
    elif failure == "wrong-id":
        span = span.model_copy(update={"span_id": "another"})
    elif failure == "unknown-basis":
        manifest["source_basis"] = {}
    elif failure == "bad-basis":
        manifest["source_basis"] = {spans[0].span_id: "invalid"}

    def read(identifier: str) -> EvidenceSpan | None:
        if failure == "denied":
            raise ResourceScopeError("RESOURCE_ACCESS_DENIED")
        return span

    reader = SelectedEvidenceReader(RecordingArtifactLedger(read))
    if failure in {"foreign", "wrong-id", "denied"}:
        with pytest.raises((RestoreError, ResourceScopeError)):
            reader.read(request, manifest)
    else:
        page = reader.read(request, manifest)
        assert page.selected_count == 1 and page.items[0].span is None
        expected = (
            "UNKNOWN_BASIS"
            if failure == "unknown-basis"
            else "UNAVAILABLE"
            if failure == "missing"
            else "BASIS_MISMATCH"
        )
        assert page.items[0].availability == expected and page.items[0].reason_codes


def test_genuine_empty_and_missing_selection_are_distinct() -> None:
    request, manifest, _ = fixture(0)

    def unexpected_read(identifier: str) -> EvidenceSpan | None:
        pytest.fail("Unexpected source read")

    reader = SelectedEvidenceReader(RecordingArtifactLedger(unexpected_read))
    assert reader.read(request, manifest).selected_count == 0
    del manifest["source_refs"]
    with pytest.raises(RestoreError, match="SELECTED_EVIDENCE_SELECTION_UNAVAILABLE"):
        reader.read(request, manifest)


@pytest.mark.parametrize(
    "fields",
    [
        {"result_revision_digest": None},
        {"selected_evidence_limit": 0},
        {"selected_evidence_limit": 101},
        {"selected_evidence_offset": -1},
    ],
)
def test_invalid_page_inputs_are_rejected(fields: dict[str, object]) -> None:
    request, _, _ = fixture(0)
    with pytest.raises(ValidationError):
        HistoricalResultInput.model_validate({**request.model_dump(), **fields})
