"""Real challenger/reviewer records with controlled I/O and finalization."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, Mock

from pydantic import JsonValue
from tests.unit.services.acquisition_coordinator_support import (
    HASH,
    T0,
    Ids,
    acquisition,
    span,
    staged_evidence,
)

from thoth.application.services.connector_service import ConnectorAcquisition, ConnectorService
from thoth.application.services.critical_counter_search import CounterSearchBasis
from thoth.application.services.critical_counter_search_coordinator import (
    CriticalCounterSearchCoordinator,
    CriticalCounterSearchExecution,
)
from thoth.application.services.critical_counter_search_finalization import (
    CounterSearchFinalizationService,
)
from thoth.application.services.evidence_graph_service import EvidenceGraphService
from thoth.application.services.investigation_service import InvestigationService
from thoth.application.services.research_identity_service import (
    ResearchContext,
    ResearchIdentityService,
)
from thoth.domain.acquisition import AcquisitionLead, EvidenceAtomicCommit
from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.counterevidence import (
    CounterSearchBudgetState,
    CounterWaveRecord,
    IndependentGateReview,
)
from thoth.domain.enums import (
    AuthorityState,
    CausalDepth,
    CausalLocus,
    HypothesisStatus,
    PortfolioStatus,
    SecurityClass,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.hypothesis import Hypothesis, HypothesisPortfolio
from thoth.domain.policy import (
    AuthoritativeExecutionPolicy,
    CounterSearchLimits,
    CounterSearchRoute,
)
from thoth.domain.project import Project, WorkThread
from thoth.ports.acquisition import AcquisitionTraceStorePort, EvidenceUnitOfWorkPort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.governance import GovernanceStorePort, ProjectPolicyReaderPort
from thoth.ports.investigation import InvestigationStorePort
from thoth.ports.ledger import LedgerPort


class Clock:
    def __init__(self) -> None:
        self.seconds = 0

    def now(self) -> datetime:
        return T0 + timedelta(seconds=self.seconds)


class Harness(CriticalCounterSearchCoordinator):
    def __init__(self, texts: tuple[str, ...], *, confirmations: int = 2) -> None:
        self.events: list[str] = []
        self.requests: list[ConnectorAccessRequest] = []
        self.committed: list[EvidenceAtomicCommit] = []
        self.commit_attempts = 0
        self.fail_commit_at: int | None = None
        self.fail_acquire_at: int | None = None
        self.failure: BaseException | None = None
        self.head_change_after: int | None = None
        self.advance_seconds = 0
        self.clock = Clock()
        self.heads = {"HYPOTHESIS:portfolio:1": HASH, "DECISION_OBJECT:object:1": HASH}
        self.detached: list[str] = []
        self.finalized: dict[str, object] | None = None
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
            working_head_digest=HASH,
        )
        self.leading = Hypothesis(
            hypothesis_id="hypothesis:1",
            object_id="object:1",
            statement="Candidate",
            observed_problem="Mismatch",
            primary_locus=CausalLocus.INPUT_MATERIAL_DATA,
            causal_depth=CausalDepth.INTERMEDIATE,
            scope_conditions={},
            support_evidence_refs=("span:primary",),
            counterevidence_refs=(),
            missing_evidence=("primary",),
            counterevidence_queries=("counter",),
            assumptions=(),
            uncertainty="unknown",
            predicted_observations=(),
            discriminating_tests=(),
            status=HypothesisStatus.DRAFT,
        )
        self.portfolio = HypothesisPortfolio(
            portfolio_id="portfolio:1",
            object_id="object:1",
            hypotheses=(self.leading,),
            status=PortfolioStatus.DRAFT,
            generated_from_head_set=HASH,
            alternatives_considered=("alternative",),
            next_checks=("check",),
            uncertainty_reserve="unknown",
        )
        self.routes = tuple(
            CounterSearchRoute(
                route_id=f"route:{i}",
                target_loci=(CausalLocus.INPUT_MATERIAL_DATA.value,),
                connector_id="fixture-reader",
                selector={"index": i},
                query_families=("independent", "alternative"),
                source_territory=f"territory:{i}",
                independence_group=f"group:{i}",
                alternative_explanation="other mechanism",
                support_match_terms=("supports",),
                counter_match_terms=("counter",),
                priority=100 - i * 10,
                estimated_cost_microunits=1,
                checkpoint_after=True,
            )
            for i in range(len(texts))
        )
        self.policy = AuthoritativeExecutionPolicy(
            policy_id="policy:p",
            project_id="p",
            policy_revision=1,
            policy_digest=HASH,
            connector_allowlist=("fixture-reader",),
            connector_allowed_egress_classes=("NONE",),
            max_source_security_class=SecurityClass.RESTRICTED,
            sandbox_runtime_allowlist=(),
            sandbox_network_policy="DENY_ALL",
            sandbox_allowed_hosts=(),
            counter_search_routes=self.routes,
            counter_search_limits=CounterSearchLimits(
                max_waves=6,
                max_results=20,
                max_documents=6,
                max_bytes=10000,
                max_tool_calls=12,
                max_cost_microunits=100,
                max_time_seconds=30,
                min_independent_confirmations=confirmations,
            ),
        )
        self.basis = CounterSearchBasis(
            HASH,
            HASH,
            ResearchContext(
                project_id="p",
                object_id="object:1",
                heads=dict(self.heads),
                portfolio_id="portfolio:1",
            ),
        )
        self.acquisitions: list[ConnectorAcquisition] = []
        for i, text in enumerate(texts):
            base = acquisition((span(f"span:{i}", text),))
            self.acquisitions.append(
                replace(
                    base,
                    ingestion=base.ingestion.model_copy(
                        update={"object_digest": hashlib.sha256(text.encode()).hexdigest()}
                    ),
                    source=base.source.model_copy(
                        update={
                            "source_id": f"source:{i}",
                            "lineage_root_id": f"lineage:{i}",
                            "authority_status": AuthorityState.OFFICIAL,
                        }
                    ),
                    binding=base.binding.model_copy(update={"binding_id": f"binding:{i}"}),
                )
            )
        connectors = Mock(spec=ConnectorService)

        async def acquire(request: ConnectorAccessRequest) -> ConnectorAcquisition:
            self.events.append("io.enter")
            self.requests.append(request)
            await asyncio.sleep(0)
            if len(self.requests) == self.fail_acquire_at:
                assert self.failure is not None
                self.events.append("io.raise")
                raise self.failure
            index = int(str(request.selector["index"]))
            result = self.acquisitions[index]
            assert result.receipt.byte_size <= request.max_bytes
            self.clock.seconds += self.advance_seconds
            if len(self.requests) == self.head_change_after:
                self.heads["HYPOTHESIS:portfolio:1"] = "b" * 64
            self.events.append("io.return")
            return result

        connectors.acquire_one = AsyncMock(side_effect=acquire)
        graph = Mock(spec=EvidenceGraphService)

        def stage(**kwargs: object) -> EvidenceAtomicCommit:
            self.events.append("stage")
            selected = cast(tuple[str, ...], kwargs["span_ids"])[0]
            candidate = next(
                s
                for a in self.acquisitions
                for s in a.ingestion.evidence_candidates
                if s.span_id == selected
            )
            return staged_evidence(cast(AcquisitionLead, kwargs["lead"]), candidate)

        graph.stage_link.side_effect = stage
        uow = Mock(spec=EvidenceUnitOfWorkPort)

        def commit(value: EvidenceAtomicCommit) -> None:
            self.events.append("commit")
            self.commit_attempts += 1
            if self.commit_attempts == self.fail_commit_at:
                raise RuntimeError("controlled commit failure")
            self.committed.append(value)

        uow.commit.side_effect = commit
        governance = Mock(spec=GovernanceStorePort)

        def detach(project: str, binding: str, *, state: str, updated_at: str) -> None:
            assert project == "p" and state == "DETACHED" and updated_at
            self.events.append("detach:" + binding)
            self.detached.append(binding)

        governance.set_source_binding_state.side_effect = detach
        ledger = Mock(spec=LedgerPort)

        def read_heads(project: str) -> dict[str, str]:
            assert project == "p"
            return dict(self.heads)

        ledger.read_heads.side_effect = read_heads
        artifacts = Mock(spec=ArtifactLedgerPort)
        primary_span = span("span:primary", "primary observation").model_copy(
            update={"artifact_id": "artifact:primary"}
        )
        primary_source = acquisition((primary_span,)).source.model_copy(
            update={
                "artifact_id": "artifact:primary",
                "source_id": "source:primary",
                "connector_ref": "primary-reader",
                "lineage_root_id": "primary-lineage",
                "authority_status": AuthorityState.OFFICIAL,
            }
        )

        def read_evidence(identifier: str) -> EvidenceSpan | None:
            return primary_span if identifier == "span:primary" else None

        artifacts.read_evidence.side_effect = read_evidence
        evidence_store = Mock(spec=EvidenceGraphStorePort)
        evidence_store.read_source_by_artifact.return_value = primary_source
        store = Mock(spec=InvestigationStorePort)
        store.update.return_value = True
        ids = Ids()
        super().__init__(
            connectors=cast(ConnectorService, connectors),
            evidence_graph=cast(EvidenceGraphService, graph),
            evidence_unit_of_work=uow,
            evidence_store=evidence_store,
            artifacts=artifacts,
            investigations=InvestigationService(store=store, clock=self.clock, ids=ids),
            traces=Mock(spec=AcquisitionTraceStorePort),
            governance=governance,
            policies=Mock(spec=ProjectPolicyReaderPort),
            ledger=ledger,
            research=cast(ResearchIdentityService, Mock(spec=ResearchIdentityService)),
            clock=self.clock,
            ids=ids,
        )
        finalizer = Mock(spec=CounterSearchFinalizationService)

        def finish(**kwargs: object) -> dict[str, JsonValue]:
            self.events.append("finalize")
            self.finalized = kwargs
            return {
                "loop_terminal": str(kwargs["loop_terminal"]),
                "budget": cast(CounterSearchBudgetState, kwargs["budget"]).model_dump(mode="json"),
                "waves": [
                    w.model_dump(mode="json")
                    for w in cast(list[CounterWaveRecord], kwargs["waves"])
                ],
                "gate": cast(IndependentGateReview, kwargs["gate"]).model_dump(mode="json"),
            }

        finalizer.multi.side_effect = finish
        self._finalizer = cast(CounterSearchFinalizationService, finalizer)

    async def run(self) -> CriticalCounterSearchExecution:
        return await self._execute_multi_wave(
            project=self.project,
            thread=self.thread,
            portfolio=self.portfolio,
            leading=self.leading,
            policy=self.policy,
            routes=self.routes,
            basis=self.basis,
        )
