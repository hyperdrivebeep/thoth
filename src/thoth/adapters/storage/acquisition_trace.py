from __future__ import annotations

from collections.abc import Callable

import orjson
from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import Connection

from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.adapters.storage.schema import (
    acquisition_search_intents,
    evidence_audit,
    evidence_leads,
    evidence_links,
    evidence_observations,
)
from thoth.adapters.storage.transaction import SqliteAtomicUnitOfWork, write_connection
from thoth.domain.acquisition import (
    AcquisitionLead,
    EvidenceAtomicCommit,
    SearchIntent,
)
from thoth.domain.canonical import model_digest
from thoth.domain.evidence_basis import source_basis, span_basis
from thoth.domain.evidence_graph import (
    EvidenceAuditRecord,
    EvidenceLinkRecord,
    ObservationRecord,
)
from thoth.ports.acquisition import AcquisitionTraceStorePort, EvidenceUnitOfWorkPort
from thoth.ports.resource_scope import ResourceScopeAdmissionPort


def _dump(value: object) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode()


class SqliteAcquisitionTraceStore(AcquisitionTraceStorePort):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def put_search_intent(self, value: SearchIntent) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(acquisition_search_intents).values(
                    search_intent_id=value.search_intent_id,
                    project_id=value.project_id,
                    investigation_id=value.investigation_id,
                    content_json=_dump(value.model_dump(mode="json")),
                    intent_digest=value.intent_digest,
                    created_at=value.created_at.isoformat(),
                )
            )

    def read_search_intent(self, search_intent_id: str) -> SearchIntent | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(acquisition_search_intents.c.content_json).where(
                        acquisition_search_intents.c.search_intent_id == search_intent_id
                    )
                )
                .mappings()
                .first()
            )
        return (
            None
            if row is None
            else SearchIntent.model_validate(orjson.loads(str(row["content_json"])))
        )

    def list_search_intents(self, investigation_id: str) -> tuple[SearchIntent, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(acquisition_search_intents.c.content_json)
                .where(acquisition_search_intents.c.investigation_id == investigation_id)
                .order_by(acquisition_search_intents.c.created_at)
            ).mappings()
            return tuple(
                SearchIntent.model_validate(orjson.loads(str(row["content_json"]))) for row in rows
            )

    def list_leads(self, investigation_id: str) -> tuple[AcquisitionLead, ...]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(evidence_leads.c.content_json)
                .where(evidence_leads.c.investigation_id == investigation_id)
                .order_by(evidence_leads.c.created_at)
            ).mappings()
            return tuple(
                AcquisitionLead.model_validate(orjson.loads(str(row["content_json"])))
                for row in rows
            )


class SqliteEvidenceUnitOfWork(EvidenceUnitOfWorkPort):
    def __init__(
        self,
        engine: Engine,
        *,
        fault_injector: Callable[[str], None] | None = None,
        resource_scopes: ResourceScopeAdmissionPort | None = None,
    ) -> None:
        self._engine = engine
        self._fault_injector = fault_injector
        self._resource_scopes = resource_scopes

    def commit(self, value: EvidenceAtomicCommit) -> None:
        with (
            SqliteAtomicUnitOfWork(self._engine, immediate=True).transaction(),
            write_connection(self._engine) as connection,
        ):
            self._validate_basis(connection, value)
            self._insert_observation(connection, value.observation)
            if self._resource_scopes is not None:
                self._resource_scopes.ensure_derived(
                    value.observation.project_id,
                    value.observation.observation_id,
                    value.observation.span_ids,
                    is_new=True,
                )
            self._fault("after_observation")
            if value.lead is not None:
                self._insert_lead(connection, value.lead)
                self._fault("after_lead")
            self._insert_link(connection, value.claim_candidate)
            if self._resource_scopes is not None:
                link = value.claim_candidate
                self._resource_scopes.ensure_derived(
                    link.project_id,
                    link.evidence_id,
                    (*link.source_ids, *link.span_ids, *link.observation_ids),
                    is_new=True,
                )
            self._fault("after_claim_candidate")
            for audit in value.audit_records:
                self._insert_audit(connection, audit)
            self._fault("after_audit")
            self._validate_basis(connection, value)
            if self._resource_scopes is not None:
                self._resource_scopes.prepare_derived_intake(
                    value.claim_candidate.project_id,
                    (*value.claim_candidate.source_ids, *value.claim_candidate.span_ids),
                )

    @staticmethod
    def _validate_basis(connection: Connection, value: EvidenceAtomicCommit) -> None:
        link, observation = value.claim_candidate, value.observation
        project = link.project_id
        basis = value.basis
        if (
            observation.project_id != project
            or observation.span_ids != link.span_ids
            or link.observation_ids != (observation.observation_id,)
            or any(audit.project_id != project for audit in value.audit_records)
            or basis.project_id != project
            or set(link.source_ids) != {row.source_id for row in basis.sources}
            or set(link.span_ids) != {row.span_id for row in basis.spans}
            or link.supersedes_evidence_id != basis.predecessor_id
        ):
            raise ValueError("EVIDENCE_COMMIT_IDENTITY_MISMATCH")
        artifacts = SqliteArtifactLedger(connection.engine)
        evidence = SqliteEvidenceGraphStore(connection.engine)
        for expected in basis.sources:
            artifact = artifacts.read_artifact(expected.artifact_id)
            source = evidence.read_source_by_artifact(expected.artifact_id)
            if artifact is None or source is None or source_basis(artifact, source) != expected:
                raise ValueError("EVIDENCE_SOURCE_REVISION_CONFLICT")
        for expected in basis.spans:
            span = artifacts.read_evidence(expected.span_id)
            if span is None or span_basis(span) != expected:
                raise ValueError("EVIDENCE_SPAN_REVISION_CONFLICT")
        if value.lead is not None and (
            value.lead.project_id != project
            or value.lead.source_id not in link.source_ids
            or value.lead.span_id not in link.span_ids
        ):
            raise ValueError("EVIDENCE_LEAD_BINDING_MISMATCH")
        if link.supersedes_evidence_id is not None:
            previous = evidence.read_link(link.supersedes_evidence_id)
            if (
                previous is None
                or model_digest("EVIDENCE_LINK_BASIS", previous, schema_version="1.0.0")
                != basis.predecessor_digest
            ):
                raise ValueError("EVIDENCE_LINK_REVISION_CONFLICT")
            predecessor = (
                connection.execute(
                    select(evidence_links).where(
                        evidence_links.c.evidence_id == link.supersedes_evidence_id,
                        evidence_links.c.project_id == project,
                    )
                )
                .mappings()
                .first()
            )
            successor = connection.execute(
                select(evidence_links.c.evidence_id).where(
                    evidence_links.c.project_id == project,
                    evidence_links.c.supersedes_evidence_id == link.supersedes_evidence_id,
                    evidence_links.c.evidence_id != link.evidence_id,
                )
            ).first()
            if (
                predecessor is None
                or successor is not None
                or link.revision != predecessor["revision"] + 1
            ):
                raise ValueError("EVIDENCE_LINK_REVISION_CONFLICT")

    @staticmethod
    def _insert_observation(connection: Connection, value: ObservationRecord) -> None:
        connection.execute(
            insert(evidence_observations).values(
                observation_id=value.observation_id,
                project_id=value.project_id,
                span_ids_json=_dump(value.span_ids),
                observed_statement=value.observed_statement,
                observed_time=(
                    None if value.observed_time is None else value.observed_time.isoformat()
                ),
                valid_time=None if value.valid_time is None else value.valid_time.isoformat(),
                observer_group=value.observer_group,
                independence_basis=value.independence_basis,
                provenance_class=value.provenance_class,
                contamination_note=value.contamination_note,
                supersedes_observation_id=value.supersedes_observation_id,
                observation_digest=value.observation_digest,
                created_at=value.created_at.isoformat(),
            )
        )

    @staticmethod
    def _insert_lead(connection: Connection, value: AcquisitionLead) -> None:
        connection.execute(
            insert(evidence_leads).values(
                lead_id=value.lead_id,
                project_id=value.project_id,
                investigation_id=value.investigation_id,
                search_intent_id=value.search_intent_id,
                source_id=value.source_id,
                span_id=value.span_id,
                state=value.state,
                content_json=_dump(value.model_dump(mode="json")),
                lead_digest=value.lead_digest,
                created_at=value.created_at.isoformat(),
            )
        )

    @staticmethod
    def _insert_link(connection: Connection, value: EvidenceLinkRecord) -> None:
        connection.execute(
            insert(evidence_links).values(
                evidence_id=value.evidence_id,
                project_id=value.project_id,
                thread_id=value.thread_id,
                target_type=value.target_type,
                target_id=value.target_id,
                relation=value.relation,
                source_ids_json=_dump(value.source_ids),
                span_ids_json=_dump(value.span_ids),
                observation_ids_json=_dump(value.observation_ids),
                conditions_json=_dump(value.conditions),
                applicability=value.applicability,
                independence_group=value.independence_group,
                support_status=value.support_status.value,
                authority_status=value.authority_status.value,
                verification_status=value.verification_status.value,
                cutoff_eligibility=value.cutoff_eligibility.value,
                content_trust=value.content_trust,
                conflict_ids_json=_dump(value.conflict_ids),
                supersedes_evidence_id=value.supersedes_evidence_id,
                revision=value.revision,
                evidence_digest=value.evidence_digest,
                created_at=value.created_at.isoformat(),
            )
        )

    @staticmethod
    def _insert_audit(connection: Connection, value: EvidenceAuditRecord) -> None:
        connection.execute(
            insert(evidence_audit).values(
                audit_id=value.audit_id,
                project_id=value.project_id,
                evidence_ref=value.evidence_ref,
                event_type=value.event_type,
                payload_json=_dump(value.payload),
                event_digest=value.event_digest,
                created_at=value.created_at.isoformat(),
            )
        )

    def _fault(self, step: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(step)
