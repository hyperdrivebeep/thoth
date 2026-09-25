from __future__ import annotations

import json
from typing import cast

from thoth.application.services.evidence_publication_basis import (
    capture_basis,
    require_same_sources,
)
from thoth.domain.acquisition import AcquisitionLead, EvidenceAtomicCommit
from thoth.domain.artifact import ArtifactEnvelope
from thoth.domain.canonical import canonical_payload, domain_digest, model_digest
from thoth.domain.enums import AuthorityState, CutoffState, SupportState, VerificationState
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_graph import (
    EvidenceAuditRecord,
    EvidenceConflictRecord,
    EvidenceLinkRecord,
    EvidenceSourceRecord,
    ObservationRecord,
    SpanCorrectionRecord,
)
from thoth.ports.acquisition import EvidenceUnitOfWorkPort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.runtime import ClockPort, IdGeneratorPort


class EvidenceGraphService:
    def __init__(
        self,
        *,
        store: EvidenceGraphStorePort,
        artifacts: ArtifactLedgerPort,
        unit_of_work: EvidenceUnitOfWorkPort,
        ledger: LedgerPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._store = store
        self._artifacts = artifacts
        self._unit_of_work = unit_of_work
        self._ledger = ledger
        self._clock = clock
        self._ids = ids

    def register_source(
        self,
        artifact: ArtifactEnvelope,
        *,
        connector_ref: str,
        version: str | None,
        rights: str = "UNKNOWN",
        retention: str = "PROJECT_DEFAULT",
        supersedes_source_id: str | None = None,
    ) -> EvidenceSourceRecord:
        with self._ledger.transaction():
            source, audit = self.stage_source(
                artifact,
                connector_ref=connector_ref,
                version=version,
                rights=rights,
                retention=retention,
                supersedes_source_id=supersedes_source_id,
            )
            if audit is None:
                return source
            self._store.add_source(source)
            self._store.append_audit(audit)
            return source

    def stage_source(
        self,
        artifact: ArtifactEnvelope,
        *,
        connector_ref: str,
        version: str | None,
        rights: str = "UNKNOWN",
        retention: str = "PROJECT_DEFAULT",
        supersedes_source_id: str | None = None,
    ) -> tuple[EvidenceSourceRecord, EvidenceAuditRecord | None]:
        existing = self._store.read_source_by_artifact(artifact.artifact_id)
        if existing is not None and supersedes_source_id is None:
            return existing, None
        source_id = self._ids.new("source")
        lineage_root = source_id
        parent_ids: tuple[str, ...] = ()
        if supersedes_source_id is not None:
            previous = self._store.read_source(supersedes_source_id)
            if previous is None or previous.project_id != artifact.project_id:
                raise ValueError("superseded source was not found in this project")
            self._require_current_source(artifact.project_id, previous.source_id)
            lineage_root = previous.lineage_root_id
            parent_ids = (previous.source_id,)
        draft: dict[str, object] = {
            "source_id": source_id,
            "project_id": artifact.project_id,
            "artifact_id": artifact.artifact_id,
            "connector_ref": connector_ref,
            "uri": artifact.source_uri,
            "artifact_type": artifact.media_type,
            "version": version,
            "sha256": artifact.byte_sha256,
            "authority_status": artifact.authority.value,
            "security_class": artifact.security_class.value,
            "official_copy": artifact.authority
            in {AuthorityState.OFFICIAL, AuthorityState.APPROVED},
            "rights": rights,
            "retention": retention,
            "valid_time": None,
            "snapshot_time": artifact.retrieved_at,
            "cutoff_eligibility": artifact.cutoff_state.value,
            "lineage_root_id": lineage_root,
            "parent_source_ids": parent_ids,
            "supersedes_source_id": supersedes_source_id,
            "created_at": self._clock.now(),
        }
        source = EvidenceSourceRecord.model_validate(
            {**draft, "source_digest": self._digest("EVIDENCE_SOURCE", draft)}
        )
        audit = self._build_audit(
            source.project_id,
            source.source_id,
            "evidence/sourceUpdated",
            draft,
        )
        return source, audit

    def propose_link(
        self,
        *,
        project_id: str,
        target_type: str,
        target_id: str,
        relation: str,
        span_ids: tuple[str, ...],
        observed_statement: str,
        thread_id: str | None,
        conditions: dict[str, str],
        applicability: str,
        independence_group: str,
        supersedes_evidence_id: str | None = None,
    ) -> EvidenceLinkRecord:
        with self._ledger.transaction():
            staged = self.stage_link(
                project_id=project_id,
                target_type=target_type,
                target_id=target_id,
                relation=relation,
                span_ids=span_ids,
                observed_statement=observed_statement,
                thread_id=thread_id,
                conditions=conditions,
                applicability=applicability,
                independence_group=independence_group,
                supersedes_evidence_id=supersedes_evidence_id,
            )
            self._unit_of_work.commit(staged)
            return staged.claim_candidate

    def stage_link(
        self,
        *,
        project_id: str,
        target_type: str,
        target_id: str,
        relation: str,
        span_ids: tuple[str, ...],
        observed_statement: str,
        thread_id: str | None,
        conditions: dict[str, str],
        applicability: str,
        independence_group: str,
        lead: AcquisitionLead | None = None,
        supersedes_evidence_id: str | None = None,
    ) -> EvidenceAtomicCommit:
        spans = self._spans(project_id, span_ids)
        sources = tuple(self._source_for_artifact(project_id, span.artifact_id) for span in spans)
        observation_draft: dict[str, object] = {
            "observation_id": self._ids.new("observation"),
            "project_id": project_id,
            "span_ids": span_ids,
            "observed_statement": observed_statement,
            "observer_group": independence_group,
            "independence_basis": "explicit project-scoped source lineage",
            "provenance_class": "PUBLIC_OR_PROJECT_SOURCE",
            "created_at": self._clock.now(),
        }
        observation = ObservationRecord.model_validate(
            {
                **observation_draft,
                "observation_digest": self._digest("OBSERVATION", observation_draft),
            }
        )
        revision = 0
        previous = None
        if supersedes_evidence_id is not None:
            previous = self._store.read_link(supersedes_evidence_id)
            if previous is None or previous.project_id != project_id:
                raise ValueError("superseded evidence link was not found")
            self._require_current_link(project_id, previous.evidence_id)
            revision = previous.revision + 1
        draft: dict[str, object] = {
            "evidence_id": self._ids.new("evidence-link"),
            "project_id": project_id,
            "thread_id": thread_id,
            "target_type": target_type,
            "target_id": target_id,
            "relation": relation,
            "source_ids": tuple(source.source_id for source in sources),
            "span_ids": span_ids,
            "observation_ids": (observation.observation_id,),
            "conditions": conditions,
            "applicability": applicability,
            "independence_group": independence_group,
            "support_status": SupportState.SUPPORTED_CANDIDATE.value,
            "authority_status": AuthorityState.UNCLASSIFIED.value,
            "verification_status": VerificationState.NOT_CHECKED.value,
            "cutoff_eligibility": CutoffState.UNKNOWN_TIME.value,
            "content_trust": "UNTRUSTED_EVIDENCE",
            "conflict_ids": (),
            "supersedes_evidence_id": supersedes_evidence_id,
            "revision": revision,
            "created_at": self._clock.now(),
        }
        link = EvidenceLinkRecord.model_validate(
            {**draft, "evidence_digest": self._digest("EVIDENCE_LINK", draft)}
        )
        audit = self._build_audit(
            project_id,
            link.evidence_id,
            "evidence/updated",
            draft,
        )
        return EvidenceAtomicCommit(
            basis=capture_basis(project_id, spans, sources, self._artifacts, previous),
            observation=observation,
            lead=lead,
            claim_candidate=link,
            audit_records=(audit,),
        )


    def correct_source_time(
        self,
        *,
        project_id: str,
        artifact_id: str,
        cutoff_state: CutoffState,
    ) -> EvidenceSourceRecord:
        with self._ledger.transaction():
            current = self._store.read_source_by_artifact(artifact_id)
            if current is None or current.project_id != project_id:
                raise ValueError("source record was not found in this project")
            self._require_current_source(project_id, current.source_id)
            draft = current.model_dump(mode="python")
            draft.update(
                {
                    "source_id": self._ids.new("source"),
                    "cutoff_eligibility": cutoff_state.value,
                    "parent_source_ids": (current.source_id,),
                    "supersedes_source_id": current.source_id,
                    "created_at": self._clock.now(),
                }
            )
            draft.pop("source_digest", None)
            corrected = EvidenceSourceRecord.model_validate(
                {**draft, "source_digest": self._digest("EVIDENCE_SOURCE", draft)}
            )
            self._store.add_source(corrected)
            return corrected

    def correct_source_metadata(
        self,
        *,
        project_id: str,
        source_id: str,
        rights: str | None,
        retention: str | None,
        official_copy_basis: str | None,
    ) -> EvidenceSourceRecord:
        with self._ledger.transaction():
            current = self._store.read_source(source_id)
            if current is None or current.project_id != project_id:
                raise ValueError("source record was not found in this project")
            self._require_current_source(project_id, source_id)
            draft = current.model_dump(mode="python")
            draft.update(
                {
                    "source_id": self._ids.new("source"),
                    "rights": current.rights if rights is None else rights,
                    "retention": current.retention if retention is None else retention,
                    "official_copy": (
                        current.official_copy
                        if official_copy_basis is None
                        else bool(official_copy_basis.strip())
                    ),
                    "parent_source_ids": (current.source_id,),
                    "supersedes_source_id": current.source_id,
                    "created_at": self._clock.now(),
                }
            )
            draft.pop("source_digest", None)
            corrected = EvidenceSourceRecord.model_validate(
                {**draft, "source_digest": self._digest("EVIDENCE_SOURCE", draft)}
            )
            self._store.add_source(corrected)
            self.audit(project_id, corrected.source_id, "evidence/sourceUpdated", draft)
            return corrected

    def challenge(
        self,
        *,
        project_id: str,
        evidence_ids: tuple[str, ...],
        field: str,
        reason: str,
    ) -> EvidenceConflictRecord:
        with self._ledger.transaction():
            for evidence_id in evidence_ids:
                link = self._store.read_link(evidence_id)
                if link is None or link.project_id != project_id:
                    raise ValueError("challenged evidence was not found in this project")
            draft: dict[str, object] = {
                "conflict_id": self._ids.new("evidence-conflict"),
                "project_id": project_id,
                "evidence_ids": evidence_ids,
                "field": field,
                "status": "OPEN",
                "reason": reason,
                "resolution_evidence_ids": (),
                "created_at": self._clock.now(),
                "updated_at": self._clock.now(),
            }
            conflict = EvidenceConflictRecord.model_validate(
                {**draft, "conflict_digest": self._digest("EVIDENCE_CONFLICT", draft)}
            )
            self._store.add_conflict(conflict)
            self.audit(project_id, conflict.conflict_id, "evidence/conflictUpdated", draft)
            return conflict

    def revalidate(self, project_id: str, evidence_id: str) -> EvidenceLinkRecord:
        with self._ledger.transaction():
            current = self._store.read_link(evidence_id)
            if current is None or current.project_id != project_id:
                raise ValueError("evidence link was not found in this project")
            self._require_current_link(project_id, evidence_id)
            spans = self._spans(project_id, current.span_ids)
            historical_sources = tuple(
                self._store.read_source(source_id) for source_id in current.source_ids
            )
            if any(
                source is None or source.project_id != project_id for source in historical_sources
            ):
                raise ValueError("evidence source lineage is incomplete")
            if {source.artifact_id for source in historical_sources if source is not None} != {
                span.artifact_id for span in spans
            }:
                raise ValueError("EVIDENCE_SOURCE_ARTIFACT_BINDING_MISMATCH")
            typed_sources = tuple(
                self._source_for_artifact(project_id, span.artifact_id) for span in spans
            )
            basis = capture_basis(project_id, spans, typed_sources, self._artifacts, current)
            eligible = all(span.cutoff_state == CutoffState.ELIGIBLE for span in spans)
            authority = (
                AuthorityState.OFFICIAL
                if all(
                    source.authority_status in {AuthorityState.OFFICIAL, AuthorityState.APPROVED}
                    for source in typed_sources
                )
                else AuthorityState.UNCLASSIFIED
            )
            support = (
                SupportState.SUPPORTED
                if eligible and authority == AuthorityState.OFFICIAL
                else SupportState.UNRESOLVED
            )
            draft = current.model_dump(mode="python")
            draft.update(
                {
                    "evidence_id": self._ids.new("evidence-link"),
                    "source_ids": tuple(source.source_id for source in typed_sources),
                    "support_status": support,
                    "authority_status": authority,
                    "verification_status": VerificationState.PROVENANCE_VALID,
                    "cutoff_eligibility": (
                        CutoffState.ELIGIBLE if eligible else CutoffState.PROHIBITED_CONTEXT
                    ),
                    "supersedes_evidence_id": current.evidence_id,
                    "revision": current.revision + 1,
                    "created_at": self._clock.now(),
                }
            )
            draft.pop("evidence_digest", None)
            updated = EvidenceLinkRecord.model_validate(
                {**draft, "evidence_digest": self._digest("EVIDENCE_LINK", draft)}
            )
            self._store.add_link(updated)
            self.audit(
                project_id,
                updated.evidence_id,
                "evidence/revalidated",
                {
                    **draft,
                    "source_basis_before": current.source_ids,
                    "source_basis_after": updated.source_ids,
                    "revalidation_reason": "EXPLICIT_CURRENT_METADATA_REASSESSMENT",
                },
            )
            require_same_sources(basis, self._artifacts, self._store)
            previous = self._store.read_link(current.evidence_id)
            if (
                previous is None
                or model_digest("EVIDENCE_LINK_BASIS", previous, schema_version="1.0.0")
                != basis.predecessor_digest
            ):
                raise ValueError("EVIDENCE_LINK_REVISION_CONFLICT")
            if any(
                link.supersedes_evidence_id == current.evidence_id
                and link.evidence_id != updated.evidence_id
                for link in self._store.list_links(project_id)
            ):
                raise ValueError("EVIDENCE_LINK_REVISION_CONFLICT")
            return updated

    def correct_span(
        self, project_id: str, span_id: str, corrected_text: str, reason: str
    ) -> SpanCorrectionRecord:
        with self._ledger.transaction():
            self._spans(project_id, (span_id,))
            draft: dict[str, object] = {
                "correction_id": self._ids.new("span-correction"),
                "project_id": project_id,
                "span_id": span_id,
                "corrected_text": corrected_text,
                "reason": reason,
                "created_at": self._clock.now(),
            }
            correction = SpanCorrectionRecord.model_validate(
                {**draft, "correction_digest": self._digest("SPAN_CORRECTION", draft)}
            )
            self._store.add_span_correction(correction)
            self.audit(project_id, span_id, "evidence/updated", draft)
            return correction

    def audit(
        self,
        project_id: str,
        evidence_ref: str,
        event_type: str,
        payload: dict[str, object],
    ) -> EvidenceAuditRecord:
        record = self._build_audit(project_id, evidence_ref, event_type, payload)
        self._store.append_audit(record)
        return record

    def _build_audit(
        self,
        project_id: str,
        evidence_ref: str,
        event_type: str,
        payload: dict[str, object],
    ) -> EvidenceAuditRecord:
        created_at = self._clock.now()
        # Keep nested timestamps and other typed values identical after JSON persistence.
        payload = cast(dict[str, object], json.loads(canonical_payload(payload)))
        draft: dict[str, object] = {
            "project_id": project_id,
            "evidence_ref": evidence_ref,
            "event_type": event_type,
            "payload": payload,
            "created_at": created_at,
        }
        record = EvidenceAuditRecord(
            audit_id=self._ids.new("evidence-audit"),
            project_id=project_id,
            evidence_ref=evidence_ref,
            event_type=event_type,
            payload=payload,
            event_digest=self._digest("EVIDENCE_AUDIT", draft),
            created_at=created_at,
        )
        return record

    def _require_current_source(self, project_id: str, source_id: str) -> None:
        if any(
            item.supersedes_source_id == source_id for item in self._store.list_sources(project_id)
        ):
            raise ValueError("EVIDENCE_SOURCE_REVISION_CONFLICT")

    def _require_current_link(self, project_id: str, evidence_id: str) -> None:
        if any(
            item.supersedes_evidence_id == evidence_id
            for item in self._store.list_links(project_id)
        ):
            raise ValueError("EVIDENCE_LINK_REVISION_CONFLICT")

    def _spans(self, project_id: str, span_ids: tuple[str, ...]) -> tuple[EvidenceSpan, ...]:
        spans: list[EvidenceSpan] = []
        for span_id in span_ids:
            span = self._artifacts.read_evidence(span_id)
            if span is None or span.project_id != project_id:
                raise ValueError("evidence span was not found in this project")
            spans.append(span)
        return tuple(spans)

    def _source_for_artifact(self, project_id: str, artifact_id: str) -> EvidenceSourceRecord:
        source = self._store.read_source_by_artifact(artifact_id)
        if source is None or source.project_id != project_id:
            raise ValueError("source record was not found for evidence span")
        return source

    @staticmethod
    def _digest(kind: str, value: dict[str, object]) -> str:
        return domain_digest(kind, "1.0.0", canonical_payload(value))
