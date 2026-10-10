"""Fast orchestration doubles with real typed records; SQLite rollback is tested separately."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock

from thoth.application.services.acquisition_coordinator import AcquisitionCoordinator
from thoth.application.services.connector_service import ConnectorAcquisition, ConnectorService
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.investigation_service import InvestigationService
from thoth.domain.acquisition import AcquisitionLead, EvidenceAtomicCommit, SearchIntent
from thoth.domain.artifact import ArtifactEnvelope, StructuralDocument
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorOperation,
    ConnectorReceipt,
    ConnectorRunRecord,
    NativeVersion,
    NativeVersionKind,
)
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    DimensionStatus,
    SecurityClass,
    SufficiencyStatus,
)
from thoth.domain.evidence import (
    EvidenceSpan,
    InformationSufficiencyAssessment,
    SufficiencyDimension,
)
from thoth.domain.evidence_basis import EvidenceCommitBasis, EvidenceSourceBasis, EvidenceSpanBasis
from thoth.domain.evidence_graph import (
    EvidenceAuditRecord,
    EvidenceLinkRecord,
    EvidenceSourceRecord,
    ObservationRecord,
)
from thoth.domain.governance import ProjectPolicy, SourceBinding
from thoth.domain.ingestion import IngestionResult
from thoth.domain.investigation import InvestigationRecord
from thoth.domain.project import Project, WorkThread
from thoth.ports.acquisition import AcquisitionTraceStorePort, EvidenceUnitOfWorkPort
from thoth.ports.governance import ProjectPolicyReaderPort
from thoth.ports.investigation import InvestigationStorePort

T0 = datetime(2026, 10, 8, tzinfo=UTC)
HASH = "a" * 64


class Clock:
    def now(self) -> datetime:
        return T0


class Ids:
    def __init__(self) -> None:
        self.count = 0

    def new(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}:{self.count}"


def span(identifier: str, text: str) -> EvidenceSpan:
    return EvidenceSpan.model_validate(
        {
            "span_id": identifier,
            "project_id": "p",
            "artifact_id": "artifact:1",
            "source_version_id": "version:1",
            "locator": {"page": 1},
            "exact_text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "extraction_method": "fixture",
            "support_state": "EXTRACTED",
            "authority_state": "UNCLASSIFIED",
            "verification_state": "NOT_CHECKED",
            "cutoff_state": "ELIGIBLE",
        }
    )


def route(group: str, *, max_results: int = 1) -> dict[str, object]:
    return {
        "evidence_group": group,
        "match_terms": [group],
        "connector_id": "fixture-reader",
        "selector": {"relative_path": group + ".md"},
        "query_families": [group],
        "max_waves": 1,
        "max_results": max_results,
    }


def policy(routes: list[dict[str, object]]) -> ProjectPolicy:
    return ProjectPolicy(
        policy_id="policy:p",
        project_id="p",
        version=1,
        policy_digest=HASH,
        created_at=T0,
        payload={
            "connector_allowlist": ["fixture-reader"],
            "connector_allowed_egress_classes": ["NONE"],
            "max_source_security_class": "RESTRICTED",
            "sandbox_runtime_allowlist": [],
            "sandbox_network_policy": "DENY_ALL",
            "sandbox_allowed_hosts": [],
            "acquisition_routes": routes,
        },
    )


def acquisition(spans: tuple[EvidenceSpan, ...]) -> ConnectorAcquisition:
    artifact = ArtifactEnvelope(
        artifact_id="artifact:1",
        project_id="p",
        source_uri="fixture://registry",
        media_type="text/plain",
        byte_sha256=HASH,
        authority=AuthorityState.UNCLASSIFIED,
        cutoff_state=CutoffState.ELIGIBLE,
        security_class=SecurityClass.INTERNAL,
        retrieved_at=T0,
        parser_name="fixture",
        parser_version="1",
    )
    return ConnectorAcquisition(
        ingestion=IngestionResult(
            document=StructuralDocument(
                artifact=artifact, nodes=(), extraction_coverage="COMPLETE"
            ),
            source_version_id="version:1",
            evidence_candidates=spans,
            object_digest=HASH,
        ),
        run=ConnectorRunRecord(
            connector_run_id="run:1",
            project_id="p",
            connector_id="fixture-reader",
            operation=ConnectorOperation.READ,
            state="SUCCEEDED",
            requested_scope_digest=HASH,
            policy_digest=HASH,
            started_at=T0,
            completed_at=T0,
        ),
        receipt=ConnectorReceipt(
            connector_run_id="run:1",
            project_id="p",
            connector_id="fixture-reader",
            driver_version="1",
            policy_digest=HASH,
            source_uri="fixture://registry",
            native_version=NativeVersion(kind=NativeVersionKind.CONTENT_HASH, value=HASH),
            content_sha256=HASH,
            byte_size=sum(len(s.exact_text.encode()) for s in spans),
            recorded_at=T0,
            receipt_digest=HASH,
        ),
        binding=SourceBinding(
            binding_id="binding:1",
            project_id="p",
            artifact_id="artifact:1",
            created_at=T0,
            updated_at=T0,
        ),
        source=EvidenceSourceRecord(
            source_id="source:1",
            project_id="p",
            artifact_id="artifact:1",
            connector_ref="fixture-reader",
            uri="fixture://registry",
            artifact_type="text/plain",
            sha256=HASH,
            authority_status=AuthorityState.UNCLASSIFIED,
            security_class="INTERNAL",
            cutoff_eligibility=CutoffState.ELIGIBLE,
            lineage_root_id="source:1",
            source_digest=HASH,
            created_at=T0,
        ),
    )


def staged_evidence(lead: AcquisitionLead, selected: EvidenceSpan) -> EvidenceAtomicCommit:
    return EvidenceAtomicCommit(
        basis=EvidenceCommitBasis(
            project_id="p",
            sources=(
                EvidenceSourceBasis(
                    project_id="p",
                    artifact_id="artifact:1",
                    artifact_digest=HASH,
                    source_id="source:1",
                    source_digest=HASH,
                    source_state_digest=HASH,
                ),
            ),
            spans=(
                EvidenceSpanBasis(
                    project_id="p",
                    artifact_id="artifact:1",
                    span_id=selected.span_id,
                    source_version_id="version:1",
                    span_digest=HASH,
                ),
            ),
        ),
        observation=ObservationRecord(
            observation_id="observation:1",
            project_id="p",
            span_ids=(selected.span_id,),
            observed_statement=selected.exact_text,
            observer_group="fixture-reader",
            independence_basis="fixture",
            provenance_class="PROJECT_SOURCE",
            observation_digest=HASH,
            created_at=T0,
        ),
        lead=lead,
        claim_candidate=EvidenceLinkRecord(
            evidence_id="link:1",
            project_id="p",
            thread_id="thread:1",
            target_type="DECISION_OBJECT",
            target_id="object:1",
            relation="SUPPORTS",
            source_ids=("source:1",),
            span_ids=(selected.span_id,),
            observation_ids=("observation:1",),
            independence_group="fixture-reader",
            evidence_digest=HASH,
            created_at=T0,
        ),
        audit_records=(
            EvidenceAuditRecord(
                audit_id="audit:evidence",
                project_id="p",
                evidence_ref="link:1",
                event_type="evidence/updated",
                payload={},
                event_digest=HASH,
                created_at=T0,
            ),
        ),
    )


class Harness:
    def __init__(self, spans: tuple[EvidenceSpan, ...], *, max_results: int = 1) -> None:
        self.events: list[str] = []
        self.requests: list[ConnectorAccessRequest] = []
        self.committed: list[EvidenceAtomicCommit] = []
        self.stage_inputs: list[dict[str, object]] = []
        self.acquire_failure: BaseException | None = None
        self.stage_failure: Exception | None = None
        self.commit_failure: Exception | None = None
        self.exhausted_wave = False
        self.stored_policy: ProjectPolicy | None = policy(
            [route("dataset_version", max_results=max_results)]
        )
        self.acquired = acquisition(spans)
        self.project = Project(
            project_id="p",
            name="fixture",
            cutoff_at=T0,
            overlay="fixture",
            policy_binding_ref="policy:p",
        )
        self.thread = WorkThread(
            thread_id="thread:1",
            project_id="p",
            cycle_id="cycle:1",
            problem="Question",
            scope={"workstream": "fixture"},
            working_head_digest=HASH,
        )
        dimension = SufficiencyDimension(
            status=DimensionStatus.UNKNOWN, evidence_refs=(), reason="fixture"
        )
        self.assessment = InformationSufficiencyAssessment(
            assessment_id="assessment:1",
            assessment_revision_id="revision:1",
            project_id="p",
            target_object_id="object:1",
            cutoff_at=T0,
            decision_question="Question",
            scope_identity=dimension,
            criterion_authority=dimension,
            evidence_coverage=dimension,
            comparability=dimension,
            counterevidence=dimension,
            instrumentation=dimension,
            expert_semantics=dimension,
            derived_status=(SufficiencyStatus.EVIDENCE_ACQUISITION_REQUIRED,),
            policy_version="fixture",
            input_head_set_digest=HASH,
        )
        ids, clock = Ids(), Clock()
        store = Mock(spec=InvestigationStorePort)
        store.update.return_value = True
        actual_investigations = InvestigationService(store=store, clock=clock, ids=ids)
        investigations = Mock(spec=InvestigationService)

        def start(**kwargs: object) -> InvestigationRecord:
            self.events.append("investigation.start")
            return cast(Callable[..., InvestigationRecord], actual_investigations.start)(**kwargs)

        def wave(record: InvestigationRecord, **kwargs: object) -> InvestigationRecord:
            self.events.append("investigation.wave")
            if self.exhausted_wave:
                record = record.model_copy(update={"budget_usage": record.budget})
            return cast(Callable[..., InvestigationRecord], actual_investigations.record_wave)(
                record, **kwargs
            )

        def stop(record: InvestigationRecord, *, reason: str) -> InvestigationRecord:
            self.events.append("investigation.stop:" + reason)
            return actual_investigations.stop(record, reason=reason)

        investigations.start.side_effect = start
        investigations.record_wave.side_effect = wave
        investigations.stop.side_effect = stop
        connectors = Mock(spec=ConnectorService)

        def capabilities() -> tuple[dict[str, object], ...]:
            self.events.append("connector.capabilities")
            return ({"connector_id": "fixture-reader", "driver_version": "1"},)

        async def acquire(request: ConnectorAccessRequest) -> ConnectorAcquisition:
            self.events.append("connector.await.enter")
            self.requests.append(request)
            await asyncio.sleep(0)
            if self.acquire_failure is not None:
                self.events.append("connector.await.raise")
                raise self.acquire_failure
            self.events.append("connector.await.return")
            return self.acquired

        connectors.capabilities.side_effect = capabilities
        connectors.acquire_one = AsyncMock(side_effect=acquire)
        graph = Mock(spec=EvidenceGraphService)

        def stage(**kwargs: object) -> EvidenceAtomicCommit:
            self.events.append("evidence.stage")
            self.stage_inputs.append(kwargs)
            if self.stage_failure is not None:
                raise self.stage_failure
            selected = next(
                s for s in spans if s.span_id == cast(tuple[str, ...], kwargs["span_ids"])[0]
            )
            return staged_evidence(cast(AcquisitionLead, kwargs["lead"]), selected)

        graph.stage_link.side_effect = stage
        uow = Mock(spec=EvidenceUnitOfWorkPort)

        def commit(value: EvidenceAtomicCommit) -> None:
            self.events.append("evidence.commit")
            if self.commit_failure is not None:
                raise self.commit_failure
            self.committed.append(value)

        uow.commit.side_effect = commit
        policies = Mock(spec=ProjectPolicyReaderPort)

        def read_policy(project: str) -> ProjectPolicy | None:
            assert project == "p"
            self.events.append("policy.read")
            return self.stored_policy

        policies.read_policy.side_effect = read_policy
        traces = Mock(spec=AcquisitionTraceStorePort)

        def intent(value: SearchIntent) -> None:
            self.events.append("intent.put")

        traces.put_search_intent.side_effect = intent
        self.coordinator = AcquisitionCoordinator(
            connectors=cast(ConnectorService, connectors),
            evidence_graph=cast(EvidenceGraphService, graph),
            evidence_unit_of_work=uow,
            investigations=cast(InvestigationService, investigations),
            traces=traces,
            policies=policies,
            clock=clock,
            ids=ids,
        )
