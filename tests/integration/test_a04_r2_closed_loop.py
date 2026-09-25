from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel, JsonValue
from sqlalchemy import func, select
from tests.integration.scoped_runtime import create_runtime, fixture_scope_policy
from tests.integration.test_a02_autonomous_acquisition import (
    A02Connector,
    DynamicA02Model,
    StaticModelResolver,
    request,
    value,
)
from tests.integration.test_a07_semantic_three_way_merge import commit

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.sandbox import ScriptedSandboxAdapter
from thoth.adapters.storage.schema import semantic_revisions
from thoth.apps.runtime import AppRuntime
from thoth.domain.action import (
    ActionDraft,
    ActionPlanDraft,
    ActionRiskFacts,
    DecisionAnalysis,
    DecisionCriterion,
)
from thoth.domain.enums import ModelRole, Reversibility
from thoth.domain.model import ModelRequest, ModelResult
from thoth.ports.model import ModelPort

TModel = TypeVar("TModel", bound=BaseModel)


class CallbackSandbox(ScriptedSandboxAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.callback: Callable[[], Awaitable[None]] | None = None

    async def run(self, spec):  # type: ignore[no-untyped-def]
        result = await super().run(spec)
        callback, self.callback = self.callback, None
        if callback is not None:
            await callback()
        return result


class A04R2Model(DynamicA02Model):
    async def structured(self, request: ModelRequest[TModel]) -> ModelResult[TModel]:
        if request.role != ModelRole.ACTION_PLANNER:
            return await super().structured(request)
        context = request.context_pack
        span_id = context.evidence[0].span_id
        hypothesis_ids = (
            tuple(item.hypothesis_id for item in context.previous_portfolio.hypotheses)
            if context.previous_portfolio
            else (
                "hypothesis:a02:data",
                "hypothesis:a02:method",
            )
        )
        output = ActionPlanDraft(
            plan_id="plan:a02",
            object_id=context.object_id,
            alternatives=(
                ActionDraft(
                    action_id="action:a04:sandbox",
                    object_id=context.object_id,
                    hypothesis_ids=(hypothesis_ids[0],),
                    action_family="SANDBOX_REPLAY",
                    specification="run the bounded evaluator in an isolated runtime",
                    expected_information_value="produces a process observation",
                    reversibility=Reversibility.FULL,
                    effect_facts=ActionRiskFacts(runs_untrusted_code=True),
                    effect_completeness_confirmed=True,
                    source_refs=(span_id,),
                ),
                ActionDraft(
                    action_id="action:a04:read",
                    object_id=context.object_id,
                    hypothesis_ids=(hypothesis_ids[1],),
                    action_family="READ_ONLY_ANALYSIS",
                    specification="inspect the existing report without execution",
                    expected_information_value="provides a safe comparison",
                    reversibility=Reversibility.FULL,
                    effect_facts=ActionRiskFacts(),
                    effect_completeness_confirmed=True,
                    source_refs=(span_id,),
                ),
            ),
            decision_analysis=DecisionAnalysis(
                decision="choose the next bounded action",
                criteria=(
                    DecisionCriterion(
                        criterion_id="criterion:a04:information",
                        name="bounded information gain",
                        mandatory=True,
                        rationale="prefer isolated reversible execution",
                    ),
                ),
                evaluations=(),
                uncertainty="sandbox process success is not scientific truth",
                sensitivity="R3 and R4 remain outside execution authority",
            ),
            proposed_frontier=("action:a04:sandbox", "action:a04:read"),
            plan_revision_digest=context.input_head_set_digest,
        )
        scoped = self._scope_fixture_output(output, context)
        typed = request.output_model.model_validate(scoped.model_dump(mode="python"))
        base = await super().structured(
            request.__class__(
                role=request.role,
                project_id=request.project_id,
                cutoff_at=request.cutoff_at,
                context_pack=request.context_pack,
                output_model=request.output_model,
                prompt_version=request.prompt_version,
                model_policy_ref=request.model_policy_ref,
                max_output_tokens=request.max_output_tokens,
            )
        )
        return ModelResult(
            output=typed,
            model_id="A04_R2_MODEL",
            prompt_version=request.prompt_version,
            scripted=True,
            input_digest=base.input_digest,
            output_digest=base.output_digest,
        )


def policy_payload(*, allow_sandbox: bool, include_template: bool = True) -> dict[str, object]:
    return {
        "resource_scope_policy": fixture_scope_policy().model_dump(mode="json"),
        "external_write": False,
        "physical_action": False,
        "unknown_action_tier": "R3",
        "connector_default": "DENY",
        "connector_allowlist": ["a02-readonly"],
        "connector_allowed_egress_classes": ["NONE"],
        "max_source_security_class": "RESTRICTED",
        "sandbox_runtime_allowlist": ["SCRIPTED"] if allow_sandbox else [],
        "sandbox_network_policy": "DENY_ALL",
        "sandbox_allowed_hosts": [],
        "acquisition_routes": [],
        "counter_search_routes": [],
        "sandbox_action_templates": (
            [
                {
                    "action_family": "SANDBOX_REPLAY",
                    "runtime_profile": "SCRIPTED",
                    "image_digest": "scripted:a04",
                    "argv": ["python", "-c", "print('bounded')"],
                    "network_policy": "DENY_ALL",
                    "allowed_hosts": [],
                }
            ]
            if include_template
            else []
        ),
    }


async def prepare_a04(
    tmp_path: Path,
    *,
    allow_sandbox: bool,
    include_template: bool = True,
    sandbox: ScriptedSandboxAdapter | None = None,
) -> tuple[AppRuntime, ScriptedSandboxAdapter, str, str]:
    connector = A02Connector()
    sandbox = sandbox or ScriptedSandboxAdapter()
    workspace = tmp_path / ("allowed" if allow_sandbox else "blocked")
    runtime = create_runtime(
        workspace,
        connector_registry=ConnectorRegistry((connector,)),
        sandbox_adapter=sandbox,
        model_resolver=StaticModelResolver(cast(ModelPort, A04R2Model())),
    )
    project_id = f"project:a04:{'allowed' if allow_sandbox else 'blocked'}"
    thread_id = f"thread:a04:{'allowed' if allow_sandbox else 'blocked'}"
    value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                f"{project_id}:create",
                {
                    "project_id": project_id,
                    "name": "A04 R2 closed loop",
                    "cutoff_at": "2026-09-01T00:00:00Z",
                },
            )
        )
    )
    value(
        await runtime.bus.dispatch(
            request(
                "project/policy/update",
                f"{project_id}:policy",
                {
                    "project_id": project_id,
                    "expected_revision": 0,
                    "payload": policy_payload(
                        allow_sandbox=allow_sandbox,
                        include_template=include_template,
                    ),
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
    value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                f"{project_id}:thread",
                {
                    "project_id": project_id,
                    "thread_id": thread_id,
                    "problem": "Run the next safe R2 hypothesis-discrimination action.",
                    "scope": {"workstream": "r2-closed-loop"},
                },
            )
        )
    )
    return runtime, sandbox, project_id, thread_id


@pytest.mark.asyncio
async def test_thread_input_runs_r2_sandbox_outcome_hypothesis_and_next_action_loop(
    tmp_path: Path,
) -> None:
    runtime, sandbox, project_id, thread_id = await prepare_a04(
        tmp_path,
        allow_sandbox=True,
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a04-thread-input",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        closed_loop = cast(dict[str, JsonValue], analyzed["r2_closed_loop"])
        portfolio_id = str(cast(dict[str, JsonValue], analyzed["portfolio"])["portfolio_id"])
        plan_id = str(cast(dict[str, JsonValue], analyzed["action_plan"])["plan_id"])
        outcome_id = str(cast(dict[str, JsonValue], closed_loop["outcome"])["outcome_id"])
        hypothesis_revisions = runtime.ledger.read_revisions(project_id, "HYPOTHESIS", portfolio_id)
        action_revisions = runtime.ledger.read_revisions(project_id, "ACTION", plan_id)
        outcome_revisions = runtime.ledger.read_revisions(project_id, "OUTCOME", outcome_id)
    finally:
        runtime.close()

    assert closed_loop["terminal_state"] == "COMPLETED"
    assert cast(dict[str, JsonValue], closed_loop["compiled_spec"])["action_id"] == (
        "action:a04:sandbox"
    )
    assert cast(dict[str, JsonValue], closed_loop["sandbox_result"])["state"] == "SUCCEEDED"
    validity = cast(dict[str, JsonValue], closed_loop["outcome_validity"])
    assert validity["process_state"] == "SUCCEEDED"
    assert validity["scientific_truth_state"] == "NOT_CERTIFIED"
    assert validity["criterion_state"] == "NOT_ASSESSED"
    assert cast(list[str], closed_loop["observation_refs"])
    assert cast(dict[str, JsonValue], closed_loop["hypothesis_appraisal"])["effect"] == "NEUTRAL"
    assert cast(list[str], closed_loop["next_action_order"])[0] == "action:a04:read"
    receipt = cast(dict[str, JsonValue], closed_loop["non_truth_receipt"])
    assert receipt["semantic_truth_certified"] is False
    assert len(sandbox.seen_specs) == 1
    assert sandbox.seen_specs[0].current_head_set_digest
    assert sandbox.seen_specs[0].input_context_digest
    assert len(hypothesis_revisions) == 2
    assert len(action_revisions) == 2
    assert len(outcome_revisions) == 1


@pytest.mark.asyncio
async def test_thread_input_policy_blocks_r2_before_sandbox_io(tmp_path: Path) -> None:
    runtime, sandbox, project_id, thread_id = await prepare_a04(
        tmp_path,
        allow_sandbox=False,
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a04-thread-input-blocked",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
    finally:
        runtime.close()

    closed_loop = cast(dict[str, JsonValue], analyzed["r2_closed_loop"])
    assert closed_loop["terminal_state"] == "POLICY_BLOCKED"
    assert sandbox.seen_specs == []


@pytest.mark.asyncio
async def test_thread_input_holds_r2_without_authoritative_sandbox_template(
    tmp_path: Path,
) -> None:
    runtime, sandbox, project_id, thread_id = await prepare_a04(
        tmp_path,
        allow_sandbox=False,
        include_template=False,
    )
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a04-thread-input-missing-template",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
    finally:
        runtime.close()

    closed_loop = cast(dict[str, JsonValue], analyzed["r2_closed_loop"])
    assert closed_loop["terminal_state"] == "HOLD"
    assert closed_loop["reason_code"] == "SANDBOX_TEMPLATE_REQUIRED"
    assert closed_loop["scientific_truth_state"] == "NOT_CERTIFIED"
    assert sandbox.seen_specs == []


@pytest.mark.asyncio
async def test_thread_input_holds_r2_result_when_head_changes_during_sandbox_io(
    tmp_path: Path,
) -> None:
    sandbox = CallbackSandbox()
    runtime, _sandbox, project_id, thread_id = await prepare_a04(
        tmp_path,
        allow_sandbox=True,
        sandbox=sandbox,
    )
    concurrent_head: list[str] = []
    revision_count_after_concurrent: list[int] = []

    async def advance_portfolio_head() -> None:
        heads = dict(runtime.ledger.read_heads(project_id))
        listed = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/portfolio/list",
                    "a04-current-portfolios",
                    {"project_id": project_id},
                )
            )
        )
        portfolios = cast(list[dict[str, JsonValue]], listed["portfolios"])
        assert len(portfolios) == 1
        portfolio_id = str(portfolios[0]["portfolio_id"])
        head_key = f"HYPOTHESIS:{portfolio_id}"
        parent = heads[head_key]
        revision = runtime.ledger.read_revision_by_digest(project_id, parent)
        assert revision is not None
        snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snapshot is not None
        content = cast(dict[str, JsonValue], snapshot.content)
        proposed = value(
            await runtime.bus.dispatch(
                request(
                    "revision/propose",
                    "a04-stale-propose",
                    {
                        "project_id": project_id,
                        "aggregate_id": portfolio_id,
                        "aggregate_type": "HYPOTHESIS",
                        "parent_revision_digests": [parent],
                        "candidate_content": {
                            **content,
                            "quality_gaps": ["concurrent authorized R2 update"],
                        },
                        "reason": "concurrent authorized A04 update",
                        "evidence_refs": [],
                        "actor_or_agent_ref": "agent:a04:concurrent",
                        "expected_head_digest": parent,
                        "candidate_schema_version": "1.0.0",
                    },
                )
            )
        )
        proposal = cast(dict[str, JsonValue], proposed["proposal"])
        committed = await commit(
            runtime,
            project_id=project_id,
            expected_heads=heads,
            proposal_digest=str(proposal["record_digest"]),
            suffix="a04-stale",
        )
        concurrent_head.append(cast(dict[str, str], committed["new_project_head_set"])[head_key])
        with runtime.ledger.engine.connect() as connection:
            revision_count_after_concurrent.append(
                int(
                    connection.execute(
                        select(func.count()).select_from(semantic_revisions)
                    ).scalar_one()
                )
            )

    sandbox.callback = advance_portfolio_head
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a04-thread-input-stale-head",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        closed_loop = cast(dict[str, JsonValue], analyzed["r2_closed_loop"])
        portfolio_id = str(cast(dict[str, JsonValue], analyzed["portfolio"])["portfolio_id"])
        with runtime.ledger.engine.connect() as connection:
            final_revision_count = int(
                connection.execute(
                    select(func.count()).select_from(semantic_revisions)
                ).scalar_one()
            )
    finally:
        runtime.close()

    assert closed_loop["terminal_state"] == "HOLD"
    assert closed_loop["reason_code"] == "SANDBOX_HEAD_STALE_AFTER_IO"
    assert concurrent_head
    assert revision_count_after_concurrent == [final_revision_count]
    assert runtime.ledger.read_heads(project_id)[f"HYPOTHESIS:{portfolio_id}"] == concurrent_head[0]
