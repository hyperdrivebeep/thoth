"""Expand only one page of an authorized immutable result's selected source identities."""

import hashlib

from thoth.application.services.research_history_scope import actor_scope_digest
from thoth.domain.research_codec import decode_current_result_manifest
from thoth.domain.research_history import (
    HistoricalResultInput,
    SelectedEvidenceItem,
    SelectedEvidencePage,
)
from thoth.domain.restore import RestoreError
from thoth.ports.artifact_ledger import ArtifactLedgerPort


class SelectedEvidenceReader:
    def __init__(self, artifacts: ArtifactLedgerPort) -> None:
        self.artifacts = artifacts

    def read(
        self, request: HistoricalResultInput, content: dict[str, object]
    ) -> SelectedEvidencePage:
        if request.result_revision_digest is None:
            raise RestoreError("SELECTED_EVIDENCE_RESULT_DIGEST_REQUIRED")
        if "source_refs" not in content:
            raise RestoreError("SELECTED_EVIDENCE_SELECTION_UNAVAILABLE")
        manifest = decode_current_result_manifest(content)
        if (
            manifest.request_ref.project_id != request.project_id
            or manifest.request_ref.revision_digest != request.request_revision_digest
            or manifest.request_ref.entity_id != f"request:{request.thread_id}"
        ):
            raise RestoreError("HISTORY_RESULT_BINDING_MISMATCH")
        refs = manifest.source_refs
        if len(refs) != len(set(refs)) or any(not ref for ref in refs):
            raise RestoreError("SELECTED_EVIDENCE_SELECTION_INVALID")
        offset, limit = request.selected_evidence_offset, request.selected_evidence_limit
        items: list[SelectedEvidenceItem] = []
        for span_id in refs[offset : offset + limit]:
            basis = manifest.source_basis.get(span_id)
            if basis is None:
                items.append(
                    self._unavailable(span_id, "UNKNOWN_BASIS", "SELECTED_EVIDENCE_BASIS_MISSING")
                )
                continue
            version, separator, expected_hash = basis.rpartition(":")
            if (
                not separator
                or not version
                or len(expected_hash) != 64
                or any(c not in "0123456789abcdef" for c in expected_hash)
            ):
                items.append(
                    self._unavailable(span_id, "BASIS_MISMATCH", "SELECTED_EVIDENCE_BASIS_INVALID")
                )
                continue
            # The injected ledger enforces current ACL before returning source bytes.
            span = self.artifacts.read_evidence(span_id)
            if span is None:
                items.append(
                    self._unavailable(span_id, "UNAVAILABLE", "SELECTED_EVIDENCE_NOT_FOUND")
                )
            elif span.project_id != request.project_id or span.span_id != span_id:
                raise RestoreError("HISTORY_RESULT_BINDING_MISMATCH")
            elif (
                span.source_version_id != version
                or span.text_sha256 != expected_hash
                or hashlib.sha256(span.exact_text.encode()).hexdigest() != expected_hash
            ):
                items.append(
                    self._unavailable(
                        span_id, "BASIS_MISMATCH", "SELECTED_EVIDENCE_SOURCE_BASIS_MISMATCH"
                    )
                )
            else:
                items.append(
                    SelectedEvidenceItem(span_id=span_id, availability="AVAILABLE", span=span)
                )
        end = offset + len(items)
        return SelectedEvidencePage(
            result_revision_digest=request.result_revision_digest,
            actor_scope_digest=actor_scope_digest(request.project_id),
            selected_count=len(refs),
            offset=offset,
            limit=limit,
            next_offset=end if end < len(refs) else None,
            items=tuple(items),
        )

    @staticmethod
    def _unavailable(span_id: str, availability: str, reason: str) -> SelectedEvidenceItem:
        return SelectedEvidenceItem.model_validate(
            dict(span_id=span_id, availability=availability, reason_codes=(reason,))
        )
