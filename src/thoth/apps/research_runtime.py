"""Composition of the full Hypothesis owner and its rebuildable identity index."""

from dataclasses import dataclass

from thoth.application.services.action_service import ActionService
from thoth.application.services.hypothesis_service import HypothesisService
from thoth.application.services.research_identity_service import ResearchIdentityService
from thoth.application.services.research_record_persistence import rebuild_identity_index
from thoth.application.services.revision_service import RevisionCommitService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.application.workflows.thread_cycle import ThreadCycleService
from thoth.apps.test_runtime import TestComponents, create_test_components
from thoth.ports.action import ActionStorePort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.decision_object import DecisionObjectStorePort
from thoth.ports.dependency import DependencyGraphPort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.model import ModelPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort


@dataclass(frozen=True)
class ResearchComponents:
    hypotheses: HypothesisStorePort
    actions: ActionStorePort
    hypothesis_service: HypothesisService
    action_service: ActionService
    identity: ResearchIdentityService
    tests: TestComponents | None = None


def create_research_components(
    *,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    objects: DecisionObjectStorePort,
    artifacts: ArtifactLedgerPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    governance: GovernanceStorePort | None = None,
    executions: ExecutionStorePort | None = None,
    blobs: ObjectStorePort | None = None,
    projects: ProjectStorePort | None = None,
) -> ResearchComponents:
    access = artifacts.scopes if isinstance(artifacts, ScopedArtifactLedger) else None
    store = stores.hypotheses(access)
    index = stores.research_identity
    ledger.register_projection("research-identity", index)
    rebuild_identity_index(ledger, index)
    tests = None
    if executions is not None and blobs is not None and projects is not None:
        tests = create_test_components(
            stores=stores,
            ledger=ledger,
            hypotheses=store,
            executions=executions,
            artifacts=artifacts,
            objects=blobs,
            projects=projects,
            clock=clock,
            ids=ids,
        )
    service = HypothesisService(
        store=store,
        objects=objects,
        artifacts=artifacts,
        ledger=ledger,
        commits=RevisionCommitService(ledger, clock, ids, policy_version="hypothesis:2.0.0"),
        clock=clock,
        ids=ids,
        test_lifecycle=None if tests is None else tests.lifecycle,
    )
    actions = stores.actions(access)
    identity = ResearchIdentityService(
        ledger=ledger,
        index=index,
        hypotheses=store,
        actions=actions,
        objects=objects,
        clock=clock,
        ids=ids,
        test_lifecycle=None if tests is None else tests.lifecycle,
    )
    action_service = ActionService(
        store=actions,
        objects=objects,
        hypotheses=store,
        artifacts=artifacts,
        governance=governance if governance is not None else stores.governance,
        ledger=ledger,
        commits=RevisionCommitService(ledger, clock, ids, policy_version="action:2.0.0"),
        semantic_uow=stores.atomic_uow,
        clock=clock,
        ids=ids,
    )
    return ResearchComponents(store, actions, service, action_service, identity, tests)


def create_projectpack_research_cycle(
    *,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    objects: DecisionObjectStorePort,
    artifacts: ArtifactLedgerPort,
    model: ModelPort,
    clock: ClockPort,
    ids: IdGeneratorPort,
    dependencies: DependencyGraphPort,
    policy_version: str,
) -> ThreadCycleService:
    components = create_research_components(
        stores=stores,
        ledger=ledger,
        objects=objects,
        artifacts=artifacts,
        clock=clock,
        ids=ids,
    )
    return ThreadCycleService(
        ledger=ledger,
        memory=stores.memory_candidates,
        model=model,
        commits=RevisionCommitService(ledger, clock, ids, policy_version=policy_version),
        clock=clock,
        ids=ids,
        dependencies=dependencies,
        research=components.identity,
    )
