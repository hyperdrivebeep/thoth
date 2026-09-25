"""Composition of research-test producers and their shared execution owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thoth.adapters.evaluators.research_test import default_research_test_evaluator
from thoth.application.services.action_service import ActionService
from thoth.application.services.execution_service import ExecutionService
from thoth.application.services.hypothesis_test_lifecycle import HypothesisTestLifecycle
from thoth.application.services.r2_closed_loop import R2ClosedLoopCoordinator
from thoth.application.services.r2_sandbox_compiler import R2SandboxSpecCompiler
from thoth.application.services.r2_test_lifecycle import R2TestLifecycle
from thoth.application.services.revision_service import RevisionCommitService
from thoth.application.services.sandbox_service import SandboxService
from thoth.application.services.test_validity_service import TestValidityService
from thoth.ports.action import ActionStorePort
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.execution import ExecutionStorePort
from thoth.ports.governance import ProjectPolicyReaderPort
from thoth.ports.hypothesis import HypothesisStorePort
from thoth.ports.ledger import ManagedLedgerPort
from thoth.ports.object_store import ObjectStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort, IdGeneratorPort
from thoth.ports.store_bundle import StoreBundlePort

if TYPE_CHECKING:
    from thoth.apps.research_runtime import ResearchComponents


@dataclass(frozen=True)
class TestComponents:
    lifecycle: HypothesisTestLifecycle
    validity: TestValidityService


def create_test_components(
    *,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    hypotheses: HypothesisStorePort,
    executions: ExecutionStorePort,
    artifacts: ArtifactLedgerPort,
    objects: ObjectStorePort,
    projects: ProjectStorePort,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> TestComponents:
    assessments = stores.test_validity
    evaluator = default_research_test_evaluator()
    lifecycle = HypothesisTestLifecycle(
        hypotheses=hypotheses,
        assessments=assessments,
        executions=executions,
        artifacts=artifacts,
        objects=objects,
        projects=projects,
        ledger=ledger,
        clock=clock,
        ids=ids,
    )
    validity = TestValidityService(
        store=assessments,
        lifecycle=lifecycle,
        evaluator=evaluator,
        artifacts=artifacts,
        objects=objects,
        ledger=ledger,
        clock=clock,
        ids=ids,
    )
    return TestComponents(lifecycle, validity)


def create_execution_components(
    *,
    ledger: ManagedLedgerPort,
    stores: StoreBundlePort,
    actions: ActionStorePort,
    action_service: ActionService,
    executions: ExecutionStorePort,
    artifacts: ArtifactLedgerPort,
    research: ResearchComponents,
    policies: ProjectPolicyReaderPort,
    sandbox: SandboxService,
    clock: ClockPort,
    ids: IdGeneratorPort,
) -> tuple[ExecutionService, R2ClosedLoopCoordinator]:
    execution_service = ExecutionService(
        # The canonical owner retains observed outcomes after I/O; public readers
        # use the scoped view and recheck current authority before returning data.
        store=stores.executions,
        actions=actions,
        action_service=action_service,
        artifacts=artifacts,
        ledger=ledger,
        commits=RevisionCommitService(ledger, clock, ids, policy_version="execution:2.0.0"),
        clock=clock,
        ids=ids,
    )
    compiler = R2SandboxSpecCompiler(artifacts=artifacts, policies=policies, ledger=ledger, ids=ids)
    tests = None
    if research.tests is not None:
        tests = R2TestLifecycle(
            hypotheses=research.hypotheses,
            hypothesis_service=research.hypothesis_service,
            lifecycle=research.tests.lifecycle,
            validity=research.tests.validity,
            execution_service=execution_service,
            executions=executions,
            research=research.identity,
            compiler=compiler,
            sandbox=sandbox,
            ledger=ledger,
        )
    r2 = R2ClosedLoopCoordinator(
        research=research.identity,
        compiler=compiler,
        sandbox=sandbox,
        ledger=ledger,
        policies=policies,
        clock=clock,
        ids=ids,
        test_lifecycle=tests,
    )
    return execution_service, r2
