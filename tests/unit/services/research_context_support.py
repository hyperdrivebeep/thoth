"""Real codecs and typed records, with observable read ports and controlled freshness/lifecycle."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from unittest.mock import Mock

from pytest import MonkeyPatch
from tests.unit.services.test_research_execution_steps_contract import candidate
from tests.unit.test_research_projection import make_hypothesis

from thoth.application.services.action_projection import (
    full_action,
    full_action_plan,
    full_action_portfolio,
)
from thoth.application.services.hypothesis_projection import full_hypothesis, full_portfolio
from thoth.application.services.hypothesis_test_lifecycle import HypothesisTestLifecycle
from thoth.application.services.research_freshness import ResearchFreshnessService
from thoth.application.services.research_identity_service import (
    ResearchContext,
    ResearchIdentityService,
)
from thoth.domain.action import ActionPlan, DecisionAnalysis
from thoth.domain.actor import ActorRef
from thoth.domain.base import DomainModel
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.decision_object_full import DecisionObjectRecord
from thoth.domain.enums import ActorKind, CausalLocus, EntityType, PortfolioStatus, RiskTier
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.hypothesis_full import HypothesisRecord, PredictionRecord
from thoth.domain.research_basis import BasisCurrentness
from thoth.domain.research_execution import ResearchBoundary, ResearchWork, research_work
from thoth.domain.research_identity import ResearchIdentity, decode_research
from thoth.domain.research_reference import RevisionRef
from thoth.domain.revision import EntitySnapshot, SemanticRevision
from thoth.domain.test_validity import TestValidityAssessment, hypothesis_semantic_digest
from thoth.ports.action import ActionStorePort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import LedgerPort
from thoth.ports.research_identity import ResearchIdentityStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort

T0 = datetime(2026, 9, 1, tzinfo=UTC)
PROJECT_ID, OBJECT_ID, HASH = "p", "object:identity", "a" * 64


def hypothesis(
    identifier: str = "h1", portfolio: str = "hp", object_id: str = OBJECT_ID
) -> HypothesisRecord:
    view = make_hypothesis(identifier, CausalLocus.INPUT_MATERIAL_DATA).model_copy(
        update={"object_id": object_id}
    )
    record = full_hypothesis(
        view,
        project_id=PROJECT_ID,
        portfolio_id=portfolio,
        revision_id="rev:" + identifier,
        created_at=T0,
        parent=None,
    )
    record = record.model_copy(update={"prediction_refs": ("prediction",)})
    return record.model_copy(
        update={
            "revision_digest": domain_digest(
                "HYPOTHESIS_RECORD",
                "2.0.0",
                canonical_payload(record.model_dump(mode="python", exclude={"revision_digest"})),
            )
        }
    )


def portfolio(identifier: str = "hp") -> HypothesisPortfolio:
    return HypothesisPortfolio(
        portfolio_id=identifier,
        object_id=OBJECT_ID,
        hypotheses=(
            make_hypothesis("h1", CausalLocus.INPUT_MATERIAL_DATA),
            make_hypothesis("h2", CausalLocus.MEASUREMENT_OBSERVATION),
        ),
        status=PortfolioStatus.DRAFT,
        generated_from_head_set=HASH,
    )


def action_plan(identifier: str = "plan") -> ActionPlan:
    return ActionPlan(
        plan_id=identifier,
        object_id=OBJECT_ID,
        alternatives=(
            candidate().model_copy(update={"object_id": OBJECT_ID}),
            candidate("b", tier=RiskTier.R0).model_copy(update={"object_id": OBJECT_ID}),
        ),
        decision_analysis=DecisionAnalysis(
            decision="fixture",
            criteria=(),
            evaluations=(),
            uncertainty="unknown",
            sensitivity="unknown",
        ),
        frontier=("a",),
        plan_revision_digest=HASH,
    )


def assessment(hypothesis_record: HypothesisRecord, **updates: object) -> TestValidityAssessment:
    data: dict[str, object] = {
        "assessment_id": "test",
        "project_id": PROJECT_ID,
        "object_id": OBJECT_ID,
        "hypothesis_id": hypothesis_record.hypothesis_id,
        "hypothesis_revision_digest": hypothesis_record.revision_digest,
        "hypothesis_semantic_digest": hypothesis_semantic_digest(
            hypothesis_record.model_dump(mode="python")
        ),
        "prediction_id": "prediction",
        "prediction_digest": HASH,
        "execution_ref": "execution",
        "attempt_ref": "attempt",
        "plan_id": "plan",
        "plan_revision_digest": HASH,
        "observation_refs": (),
        "measurement_contract_ref": "contract",
        "measurement_contract_digest": HASH,
        "sandbox_receipt_digest": HASH,
        "input_context_digest": HASH,
        "policy_digest": HASH,
        "knowledge_cutoff": T0,
        "scope": {},
        "test_validity": "VALID",
        "prediction_fit": "MATCH",
        "reasons": (),
        "producer_ref": "producer",
        "runtime_profile": "SCRIPTED",
        "execution_security_tier": "R2",
        "raw_observation_digest": HASH,
        "measurement_output_digest": HASH,
        "substantive_update_allowed": False,
        "created_at": T0,
        "assessment_digest": HASH,
    }
    return TestValidityAssessment.model_validate({**data, **updates})


class Captures(dict[str, str]):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def setdefault(self, key: str, default: str = "") -> str:
        self.events.append("capture:" + key)
        return super().setdefault(key, default)


class WorkContext(dict[str, object]):
    def __init__(self, events: list[str]) -> None:
        super().__init__(sentinel="kept")
        self.events = events

    def __setitem__(self, key: str, value: object) -> None:
        if key == "entity_currentness":
            self.events.append("work.currentness")
        super().__setitem__(key, value)


class Harness:
    def __init__(
        self, patch: MonkeyPatch, *, lifecycle: bool = True, work_project: str = PROJECT_ID
    ) -> None:
        self.events: list[str] = []
        self.heads = {f"DECISION_OBJECT:{OBJECT_ID}": HASH, "CRITERION:ignored": "f" * 64}
        self.revisions: dict[str, SemanticRevision] = {}
        self.snapshots: dict[str, EntitySnapshot] = {}
        self.labels: dict[str, str] = {}
        self.indexed: dict[str, ResearchIdentity] = {}
        self.states: dict[str, BasisCurrentness] = {}
        self.typed: dict[str, str] = {}
        self.object: DecisionObjectRecord | None = DecisionObjectRecord.model_construct(
            revision_digest=HASH
        )
        self.missing_prediction = False
        self.fault_at: str | None = None
        self.fault = RuntimeError("controlled read failure")
        self.work = ResearchWork(
            request_ref=RevisionRef(
                project_id=work_project,
                entity_type="THREAD",
                entity_id="t",
                revision_id="request",
                revision_digest=HASH,
            ),
            effective_question="fixture",
            boundary=cast(ResearchBoundary, Mock(spec=ResearchBoundary)),
            consumed_heads=Captures(self.events),
            context=WorkContext(self.events),
        )

        def event(name: str) -> None:
            self.events.append(name)
            if self.fault_at == name:
                raise self.fault

        def object_read(
            project: str, identifier: str, digest: str | None
        ) -> DecisionObjectRecord | None:
            assert (project, identifier, digest) == (PROJECT_ID, OBJECT_ID, None)
            event("object")
            return self.object

        def revision_read(project: str, digest: str) -> SemanticRevision | None:
            assert project == PROJECT_ID
            event("revision:" + self.labels[digest])
            return self.revisions.get(digest)

        def snapshot_read(identifier: str) -> EntitySnapshot | None:
            event("snapshot:" + identifier.removeprefix("snapshot:"))
            return self.snapshots.get(identifier)

        def index_read(project: str, digest: str) -> ResearchIdentity | None:
            assert project == PROJECT_ID
            event("index:" + self.labels[digest])
            return self.indexed.get(digest)

        def freshness(
            service: ResearchFreshnessService, project: str, key: str, digest: str
        ) -> BasisCurrentness:
            assert project == PROJECT_ID and self.heads[key] == digest
            event("freshness:" + key)
            return self.states.get(key, BasisCurrentness(state="CURRENT"))

        def typed(name: str) -> Callable[[str, str, str | None], None]:
            def read(project: str, identifier: str, digest: str | None) -> None:
                assert project == PROJECT_ID and digest is not None
                event(name + ":" + identifier)

            return read

        def require_assessment(project: str, identifier: str) -> None:
            assert project == PROJECT_ID
            event("assessment:" + identifier)

        def prediction_read(identifier: str) -> PredictionRecord | None:
            event("prediction:" + identifier)
            return (
                None
                if self.missing_prediction
                else PredictionRecord.model_construct(prediction_id=identifier)
            )

        def require_prediction(prediction: PredictionRecord, hypothesis: HypothesisRecord) -> None:
            assert prediction.prediction_id in hypothesis.prediction_refs
            event("prediction.require")

        ledger, index = Mock(spec=LedgerPort), Mock(spec=ResearchIdentityStorePort)
        hyp, actions, objects = (
            Mock(spec=HypothesisStorePort),
            Mock(spec=ActionStorePort),
            Mock(spec=DecisionObjectStorePort),
        )
        ledger.read_revision_by_digest.side_effect = revision_read
        ledger.read_snapshot.side_effect = snapshot_read
        index.read.side_effect = index_read
        objects.read_object.side_effect = object_read
        hyp.read_hypothesis.side_effect, hyp.read_portfolio.side_effect = (
            typed("hypothesis"),
            typed("hypothesis.portfolio"),
        )
        (
            actions.read_action.side_effect,
            actions.read_portfolio.side_effect,
            actions.read_plan.side_effect,
        ) = typed("action"), typed("action.portfolio"), typed("action.plan")
        hyp.read_prediction.side_effect = prediction_read
        tests = Mock(spec=HypothesisTestLifecycle)
        tests.require_assessment.side_effect, tests.require_prediction.side_effect = (
            require_assessment,
            require_prediction,
        )
        patch.setattr(ResearchFreshnessService, "evaluate_entity", freshness)
        self.service = ResearchIdentityService(
            ledger=ledger,
            index=index,
            hypotheses=hyp,
            actions=actions,
            objects=objects,
            clock=Mock(spec=ClockPort),
            ids=Mock(spec=IdGeneratorPort),
            test_lifecycle=cast(HypothesisTestLifecycle, tests) if lifecycle else None,
        )

    def add(
        self,
        kind: EntityType,
        identifier: str,
        record: DomainModel | dict[str, object],
        typed: str | None = None,
    ) -> str:
        key = f"{kind.value}:{identifier}"
        content = record.model_dump(mode="python") if isinstance(record, DomainModel) else record
        digest = domain_digest(
            "FIXTURE_REVISION", "1.0.0", canonical_payload({"key": key, "content": content})
        )
        snapshot = EntitySnapshot(
            snapshot_id="snapshot:" + key,
            project_id=PROJECT_ID,
            entity_type=kind,
            entity_id=identifier,
            schema_version="1.0.0",
            content=content,
            content_digest=domain_digest("ENTITY_SNAPSHOT", "1.0.0", canonical_payload(content)),
        )
        revision = SemanticRevision(
            revision_id="revision:" + key,
            project_id=PROJECT_ID,
            entity_type=kind,
            entity_id=identifier,
            snapshot_id=snapshot.snapshot_id,
            parent_revision_digests=(),
            actor=ActorRef(actor_id="fixture", kind=ActorKind.AGENT, role="fixture"),
            reason="fixture",
            evidence_refs=(),
            affected_refs=(),
            revision_digest=digest,
            created_at=T0,
        )
        self.heads[key], self.labels[digest] = digest, key
        self.revisions[digest], self.snapshots[snapshot.snapshot_id] = revision, snapshot
        self.indexed[digest] = decode_research(revision, snapshot).identity
        if typed is not None:
            self.typed[key] = typed + ":" + identifier
        return digest

    def standard(self, *, legacy: bool = False) -> HypothesisRecord:
        first, second = hypothesis(), hypothesis("h2")
        self.add(EntityType.HYPOTHESIS, "h2", second, "hypothesis")
        self.add(EntityType.HYPOTHESIS, "h1", first, "hypothesis")
        hp = portfolio()
        self.add(
            EntityType.HYPOTHESIS,
            "hp",
            hp
            if legacy
            else full_portfolio(
                hp, project_id=PROJECT_ID, revision_id="hp-rev", created_at=T0, parent=None
            ),
            None if legacy else "hypothesis.portfolio",
        )
        plan = action_plan()
        records = tuple(
            full_action(
                a,
                project_id=PROJECT_ID,
                portfolio_id="ap",
                revision_id="rev:" + a.action_id,
                created_at=T0,
                parent=None,
            )
            for a in plan.alternatives
        )
        for a in records:
            self.add(EntityType.ACTION, a.action_id, a, "action")
        self.add(
            EntityType.ACTION,
            "ap",
            full_action_portfolio(
                plan,
                project_id=PROJECT_ID,
                portfolio_id="ap",
                revision_id="ap-rev",
                created_at=T0,
                parent=None,
                retained_refs=(),
            ),
            "action.portfolio",
        )
        self.add(
            EntityType.ACTION,
            "plan",
            plan
            if legacy
            else full_action_plan(
                plan,
                project_id=PROJECT_ID,
                portfolio_id="ap",
                revision_id="plan-rev",
                created_at=T0,
                parent=None,
                actions=records,
            ),
            None if legacy else "action.plan",
        )
        return first

    def read(self) -> ResearchContext:
        token = research_work.set(self.work)
        try:
            return self.service.context(PROJECT_ID, OBJECT_ID, self.heads)
        finally:
            research_work.reset(token)
