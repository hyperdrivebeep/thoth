from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel, JsonValue
from sqlalchemy import func, select
from tests.integration.scoped_runtime import fixture_scope_policy

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.storage.schema import (
    evidence_audit,
    evidence_leads,
    evidence_links,
    evidence_observations,
)
from thoth.apps import runtime as runtime_module
from thoth.apps.runtime import AppRuntime
from thoth.domain.action import (
    ActionDraft,
    ActionPlanDraft,
    ActionRiskFacts,
    DecisionAnalysis,
    DecisionCriterion,
)
from thoth.domain.canonical import canonical_payload, domain_digest, model_digest
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCapability,
    ConnectorCheckpoint,
    ConnectorFetchResult,
    ConnectorOperation,
    ConnectorSelectorContract,
    NativeVersion,
    NativeVersionKind,
    SelectorFieldSpec,
)
from thoth.domain.enums import (
    CausalDepth,
    CausalLocus,
    HypothesisStatus,
    ModelRole,
    PortfolioStatus,
    Reversibility,
    RiskTier,
)
from thoth.domain.hypothesis import DiscriminatingTest, Hypothesis, HypothesisPortfolio
from thoth.domain.model import ContextPack, ModelRequest, ModelResult
from thoth.ports.model import ModelPort
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse

TModel = TypeVar("TModel", bound=BaseModel)


def request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, JsonValue], child)


class A02Connector:
    capability = ConnectorCapability(
        connector_id="a02-readonly",
        source_kind="LOCAL",
        driver_version="a02-test-v1",
        operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
        native_version_kinds=(NativeVersionKind.CONTENT_HASH,),
        egress_class="NONE",
        selector_contract=ConnectorSelectorContract(
            fields=(SelectorFieldSpec(name="relative_path", value_type="STRING"),),
        ),
    )

    def __init__(self) -> None:
        self.payloads = {
            "initial.md": b"# Experiment\n\nThe dataset version is missing from the report.\n",
            "catalog.md": b"# Dataset registry\n\ndataset_version: 2026.08\nstatus: approved\n",
        }
        self.discover_count = 0
        self.fetch_count = 0
        self.close_count = 0

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        self.discover_count += 1
        name = str(request.selector["relative_path"])
        raw = self.payloads[name]
        digest = hashlib.sha256(raw).hexdigest()
        return (
            ConnectorArtifactRef(
                source_uri=f"a02://{name}",
                locator={"relative_path": name},
                media_type="text/markdown",
                native_version=NativeVersion(
                    kind=NativeVersionKind.CONTENT_HASH,
                    value=digest,
                ),
                size_hint=len(raw),
                observed_at=datetime(2026, 9, 1, tzinfo=UTC),
            ),
        )

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del checkpoint
        self.fetch_count += 1
        name = str(request.selector["relative_path"])
        raw = self.payloads[name]
        return ConnectorFetchResult(
            ref=ref,
            raw=raw,
            content_sha256=hashlib.sha256(raw).hexdigest(),
        )

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
        self.close_count += 1


class DynamicA02Model:
    model_id = "A02_SEQUENCE_MODEL"

    def __init__(self) -> None:
        self._first_object: str | None = None

    def _scope_fixture_output(self, output: BaseModel, context: ContextPack) -> BaseModel:
        # The test model must not propose the same canonical IDs for different Objects.
        if self._first_object is None:
            self._first_object = context.object_id
        suffix = "" if context.object_id == self._first_object else ":" + context.object_id
        previous = context.previous_portfolio
        known_hypotheses = (
            {}
            if previous is None
            else {item.primary_locus: item.hypothesis_id for item in previous.hypotheses}
        )
        if isinstance(output, HypothesisPortfolio):
            identifier = output.portfolio_id + suffix if previous is None else previous.portfolio_id
            if previous is None and context.canonical_portfolio is not None:
                identifier = context.canonical_portfolio.portfolio_id
            return output.model_copy(
                update={
                    "portfolio_id": identifier,
                    "hypotheses": tuple(
                        item.model_copy(
                            update={
                                "hypothesis_id": known_hypotheses.get(
                                    item.primary_locus, item.hypothesis_id + suffix
                                ),
                            }
                        )
                        for item in output.hypotheses
                    ),
                }
            )
        if isinstance(output, ActionPlanDraft):
            proposed = context.candidate_portfolio or previous
            if proposed is not None:
                known_hypotheses = {
                    item.primary_locus: item.hypothesis_id for item in proposed.hypotheses
                }
            hypotheses = {
                "hypothesis:a02:data": known_hypotheses.get(
                    CausalLocus.INPUT_MATERIAL_DATA, "hypothesis:a02:data" + suffix
                ),
                "hypothesis:a02:method": known_hypotheses.get(
                    CausalLocus.METHOD_DESIGN_IMPLEMENTATION, "hypothesis:a02:method" + suffix
                ),
            }
            plan = context.previous_action_plan
            known_actions = (
                {}
                if plan is None
                else {item.action_family: item.action_id for item in plan.alternatives}
            )
            mapped = {
                item.action_id: known_actions.get(item.action_family, item.action_id + suffix)
                for item in output.alternatives
            }
            return output.model_copy(
                update={
                    "plan_id": output.plan_id + suffix if plan is None else plan.plan_id,
                    "alternatives": tuple(
                        item.model_copy(
                            update={
                                "action_id": mapped[item.action_id],
                                "hypothesis_ids": tuple(
                                    hypotheses.get(reference, reference)
                                    for reference in item.hypothesis_ids
                                ),
                            }
                        )
                        for item in output.alternatives
                    ),
                    "proposed_frontier": tuple(
                        mapped[identifier] for identifier in output.proposed_frontier
                    ),
                }
            )
        return output

    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        context = request.context_pack
        span_id = context.evidence[0].span_id
        if request.role == ModelRole.HYPOTHESIS_GENERATOR:
            output: BaseModel = HypothesisPortfolio(
                portfolio_id="portfolio:a02",
                object_id=context.object_id,
                hypotheses=(
                    self._hypothesis(
                        "hypothesis:a02:data",
                        context.object_id,
                        CausalLocus.INPUT_MATERIAL_DATA,
                        span_id,
                    ),
                    self._hypothesis(
                        "hypothesis:a02:method",
                        context.object_id,
                        CausalLocus.METHOD_DESIGN_IMPLEMENTATION,
                        span_id,
                    ),
                    self._hypothesis(
                        "hypothesis:a02:unknown",
                        context.object_id,
                        CausalLocus.OTHER_WITH_DESCRIPTION,
                        span_id,
                    ),
                ),
                status=PortfolioStatus.TESTABLE,
                generated_from_head_set=context.input_head_set_digest,
            )
        else:
            output = ActionPlanDraft(
                plan_id="plan:a02",
                object_id=context.object_id,
                alternatives=(
                    ActionDraft(
                        action_id="action:a02:analyze",
                        object_id=context.object_id,
                        hypothesis_ids=("hypothesis:a02:data",),
                        action_family="READ_ONLY_ANALYSIS",
                        specification="compare the acquired dataset version",
                        expected_information_value="closes the dataset version gap",
                        reversibility=Reversibility.FULL,
                        effect_facts=ActionRiskFacts(),
                        effect_completeness_confirmed=True,
                        source_refs=(span_id,),
                    ),
                    ActionDraft(
                        action_id="action:a02:request",
                        object_id=context.object_id,
                        hypothesis_ids=("hypothesis:a02:method",),
                        action_family="EVIDENCE_REQUEST",
                        specification="request an independent registry check",
                        expected_information_value="tests registry consistency",
                        reversibility=Reversibility.FULL,
                        effect_facts=ActionRiskFacts(),
                        effect_completeness_confirmed=True,
                        source_refs=(span_id,),
                    ),
                ),
                decision_analysis=DecisionAnalysis(
                    decision="choose the next bounded evidence check",
                    criteria=(
                        DecisionCriterion(
                            criterion_id="criterion:a02:information",
                            name="information gain",
                            mandatory=True,
                            rationale="close the decision-critical gap",
                        ),
                    ),
                    evaluations=(),
                    uncertainty="only project-authorized evidence may be used",
                    sensitivity="stop after the bounded route is exhausted",
                ),
                proposed_frontier=("action:a02:analyze", "action:a02:request"),
                plan_revision_digest=context.input_head_set_digest,
            )
        output = self._scope_fixture_output(output, context)
        typed = request.output_model.model_validate(output.model_dump(mode="python"))
        input_digest = domain_digest(
            "A02_MODEL_INPUT",
            "1.0.0",
            canonical_payload(
                {
                    "role": request.role,
                    "head": context.input_head_set_digest,
                    "evidence": tuple(item.span_id for item in context.evidence),
                }
            ),
        )
        return ModelResult(
            output=typed,
            model_id=self.model_id,
            prompt_version=request.prompt_version,
            scripted=True,
            input_digest=input_digest,
            output_digest=model_digest(
                "A02_MODEL_OUTPUT",
                typed,
                schema_version="1.0.0",
            ),
        )

    @staticmethod
    def _hypothesis(
        hypothesis_id: str,
        object_id: str,
        locus: CausalLocus,
        span_id: str,
    ) -> Hypothesis:
        return Hypothesis(
            hypothesis_id=hypothesis_id,
            object_id=object_id,
            statement=f"{locus.value} may explain the result",
            observed_problem="dataset version is unresolved",
            primary_locus=locus,
            causal_depth=CausalDepth.INTERMEDIATE,
            scope_conditions={"scope": "a02-fixture"},
            support_evidence_refs=(span_id,),
            counterevidence_refs=(),
            counterevidence_queries=(f"search against {locus.value}",),
            assumptions=("source locator is correct",),
            uncertainty="the cause is not isolated",
            predicted_observations=("the registry identifies a version",),
            discriminating_tests=(
                DiscriminatingTest(
                    test_id=f"test:{hypothesis_id}",
                    procedure_candidate="compare report and registry",
                    expected_if_true="version mismatch is visible",
                    expected_if_alternative="version is aligned",
                    risk_tier=RiskTier.R1,
                    reversibility=Reversibility.FULL,
                ),
            ),
            status=HypothesisStatus.TESTABLE,
        )


class StaticModelResolver:
    def __init__(self, model: ModelPort) -> None:
        self._model = model

    def resolve(self, *, provider: str, model: str | None) -> ModelPort:
        del provider, model
        return self._model


def policy_payload(*, allow_connector: bool) -> dict[str, object]:
    return {
        "resource_scope_policy": fixture_scope_policy().model_dump(mode="json"),
        "external_write": False,
        "physical_action": False,
        "unknown_action_tier": "R3",
        "connector_default": "DENY",
        "connector_allowlist": ["a02-readonly"] if allow_connector else [],
        "connector_allowed_egress_classes": ["NONE"],
        "max_source_security_class": "RESTRICTED",
        "sandbox_runtime_allowlist": [],
        "sandbox_network_policy": "DENY_ALL",
        "sandbox_allowed_hosts": [],
        "acquisition_routes": [
            {
                "evidence_group": "dataset_version",
                "match_terms": ["dataset_version"],
                "connector_id": "a02-readonly",
                "selector": {"relative_path": "catalog.md"},
                "query_families": ["dataset registry", "version manifest"],
                "max_waves": 1,
                "max_results": 1,
            }
        ],
    }


async def prepare_thread(
    tmp_path: Path,
    *,
    allow_connector: bool,
    evidence_fault_injector: Callable[[str], None] | None = None,
    memory_fault_injector: Callable[[str], None] | None = None,
) -> tuple[AppRuntime, A02Connector, str]:
    connector = A02Connector()
    model = DynamicA02Model()
    runtime = runtime_module.create_runtime(
        tmp_path / ("allowed" if allow_connector else "blocked"),
        connector_registry=ConnectorRegistry((connector,)),
        model_resolver=StaticModelResolver(cast(ModelPort, model)),
        evidence_fault_injector=evidence_fault_injector,
        memory_fault_injector=memory_fault_injector,
    )
    project_id = f"project:a02:{'allowed' if allow_connector else 'blocked'}"
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                f"{project_id}:create",
                {
                    "project_id": project_id,
                    "name": "A02 autonomous acquisition",
                    "cutoff_at": "2026-09-01T00:00:00Z",
                },
            )
        )
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/policy/update",
                f"{project_id}:policy:open",
                {
                    "project_id": project_id,
                    "expected_revision": 0,
                    "payload": policy_payload(allow_connector=True),
                },
            )
        )
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                f"{project_id}:source",
                {
                    "project_id": project_id,
                    "connector_id": "a02-readonly",
                    "selector": {"relative_path": "initial.md"},
                    "media_type": "text/markdown",
                    "authority": "OFFICIAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
    )
    if not allow_connector:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    f"{project_id}:policy:closed",
                    {
                        "project_id": project_id,
                        "expected_revision": 2,
                        "payload": policy_payload(allow_connector=False),
                    },
                )
            )
        )
    started = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                f"{project_id}:thread",
                {
                    "project_id": project_id,
                    "thread_id": f"thread:{project_id}",
                    "problem": "Which dataset_version produced the reported result?",
                    "scope": {"workstream": "dataset-audit"},
                },
            )
        )
    )
    assert started["execution_state"] == "IDLE"
    return runtime, connector, project_id


@pytest.mark.asyncio
async def test_thread_input_autonomously_acquires_missing_dataset_version(
    tmp_path: Path,
) -> None:
    runtime, connector, project_id = await prepare_thread(
        tmp_path,
        allow_connector=True,
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a02-thread-input",
                    {
                        "project_id": project_id,
                        "thread_id": f"thread:{project_id}",
                    },
                )
            )
        )
        sources = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/list",
                    "a02-source-list",
                    {"project_id": project_id},
                )
            )
        )
    finally:
        runtime.close()

    acquisition = cast(dict[str, JsonValue], analyzed["autonomous_acquisition"])
    investigation = cast(dict[str, JsonValue], acquisition["investigation"])
    search_intent = cast(dict[str, JsonValue], acquisition["search_intent"])
    assert acquisition["terminal_state"] == "SUFFICIENT"
    assert acquisition["reanalysis_performed"] is True
    assert investigation["mode"] == "BOUNDED"
    assert investigation["domain_state"] == "CONVERGED"
    assert search_intent["evidence_group"] == "dataset_version"
    assert search_intent["max_waves"] == 1
    assert cast(dict[str, JsonValue], acquisition["connector_run"])["state"] == "SUCCEEDED"
    assert cast(dict[str, JsonValue], acquisition["observation"])["span_ids"]
    assert cast(dict[str, JsonValue], acquisition["lead"])["state"] == "CLAIM_CANDIDATE"
    assert (
        cast(dict[str, JsonValue], acquisition["claim_candidate"])["support_status"]
        == "SUPPORTED_CANDIDATE"
    )
    assert "dataset_version" not in cast(list[str], acquisition["remaining_target_gaps"])
    assert len(cast(list[object], sources["artifacts"])) == 2
    assert len(cast(list[object], sources["bindings"])) == 2
    assert connector.discover_count == 2
    assert connector.fetch_count == 2
    assert connector.close_count == 2
    reopened = runtime_module.create_runtime(tmp_path / "allowed")
    try:
        readback = value(
            await reopened.bus.dispatch(
                request(
                    "investigation/read",
                    "a02-investigation-readback",
                    {
                        "project_id": project_id,
                        "investigation_id": investigation["investigation_id"],
                    },
                )
            )
        )
    finally:
        reopened.close()
    assert len(cast(list[object], readback["search_intents"])) == 1
    assert len(cast(list[object], readback["leads"])) == 1


@pytest.mark.asyncio
async def test_thread_input_policy_block_returns_typed_hold_without_connector_io(
    tmp_path: Path,
) -> None:
    runtime, connector, project_id = await prepare_thread(
        tmp_path,
        allow_connector=False,
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a02-thread-input-blocked",
                    {
                        "project_id": project_id,
                        "thread_id": f"thread:{project_id}",
                    },
                )
            )
        )
    finally:
        runtime.close()

    acquisition = cast(dict[str, JsonValue], analyzed["autonomous_acquisition"])
    assert acquisition["terminal_state"] == "POLICY_BLOCKED"
    assert acquisition["reanalysis_performed"] is False
    assert acquisition["minimum_question"]
    denial = cast(dict[str, JsonValue], acquisition["policy_denial"])
    assert denial["denial_basis"] == "POLICY_EMPTY_CONNECTOR_ALLOWLIST"
    assert connector.discover_count == 1
    assert connector.fetch_count == 1
    assert connector.close_count == 1


@pytest.mark.asyncio
async def test_thread_input_evidence_uow_fault_rolls_back_observation_lead_claim_and_audit(
    tmp_path: Path,
) -> None:
    def inject_fault(step: str) -> None:
        if step == "after_lead":
            raise RuntimeError("injected evidence UoW fault")

    runtime, connector, project_id = await prepare_thread(
        tmp_path,
        allow_connector=True,
        evidence_fault_injector=inject_fault,
    )

    def evidence_state() -> tuple[int, int, int, int]:
        with runtime.ledger.engine.connect() as connection:
            return (
                int(
                    connection.execute(
                        select(func.count()).select_from(evidence_observations)
                    ).scalar_one()
                ),
                int(
                    connection.execute(
                        select(func.count()).select_from(evidence_leads)
                    ).scalar_one()
                ),
                int(
                    connection.execute(
                        select(func.count()).select_from(evidence_links)
                    ).scalar_one()
                ),
                int(
                    connection.execute(
                        select(func.count())
                        .select_from(evidence_audit)
                        .where(evidence_audit.c.event_type == "evidence/updated")
                    ).scalar_one()
                ),
            )

    try:
        before = evidence_state()
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a02-thread-input-uow-fault",
                    {
                        "project_id": project_id,
                        "thread_id": f"thread:{project_id}",
                    },
                )
            )
        )
        after = evidence_state()
    finally:
        runtime.close()

    acquisition = cast(dict[str, JsonValue], analyzed["autonomous_acquisition"])
    assert acquisition["terminal_state"] == "FAILED"
    assert acquisition["reanalysis_performed"] is False
    assert before == after
    assert connector.discover_count == 2
    assert connector.fetch_count == 2
