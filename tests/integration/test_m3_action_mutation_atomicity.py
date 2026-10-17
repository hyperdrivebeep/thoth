"""Action mutation checkpoints (89a): source-defined reads and real post-write fault injection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import event, select
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.schema import metadata
from thoth.adapters.storage.sqlite import SqliteLedgerTransaction
from thoth.adapters.storage.transaction import read_connection
from thoth.protocol.deferred import current_operation

OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step89-20261014"
BOOKKEEPING = {"operations", "idempotency_keys", "events", "operation_resource_bindings"}
LEDGER_POINTS = ("insert_snapshot", "insert_revision", "set_head", "insert_receipt")
WRITERS = {
    "insert_snapshot": (SqliteLedgerTransaction, "entity_snapshots", "snapshot_id"),
    "insert_revision": (SqliteLedgerTransaction, "semantic_revisions", "revision_id"),
    "insert_receipt": (SqliteLedgerTransaction, "receipts", "receipt_id"),
    "add_action": (SqliteActionStore, "action_records", "action_revision_id"),
    "add_portfolio": (SqliteActionStore, "action_portfolios", "portfolio_revision_id"),
    "add_plan": (SqliteActionStore, "action_plans", "plan_revision_id"),
    "append_audit": (SqliteActionStore, "action_audit", "audit_id"),
}
PLAN_KINDS = ("plan_revise", "plan_revalidate", "step_add", "step_revise", "impact", "policy")
CASES = (
    [("revise", point) for point in (*LEDGER_POINTS, "add_action", "append_audit")]
    + [("create", "append_audit"), ("compensation", "append_audit")]
    + [
        ("portfolio_compose", "add_portfolio"),
        ("portfolio_compose", "append_audit"),
        ("evaluate", "extra_audit"),
        ("recommend", "extra_audit"),
        ("select", "extra_audit"),
    ]
    + [("plan_compose", "add_plan")]
    + [(kind, "append_audit") for kind in PLAN_KINDS]
    + [("merge", "proposal_insert")]
)
RECORDS = (
    ("action", "action_id", "action_revision_id"),
    ("portfolio", "portfolio_id", "portfolio_revision_id"),
    ("plan", "plan_id", "plan_revision_id"),
)


def save(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + ".json")).write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


async def call(runtime: Any, method: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return value(await runtime.bus.dispatch(request(method, key, payload)))


def revision(digest: str) -> str:
    return "revision:" + digest


def effect_vector(untrusted: bool = False) -> dict[str, Any]:
    return {
        "effect_completeness_confirmed": True,
        "runs_untrusted_code": untrusted,
        "sandbox_required": untrusted,
        "external_write": False,
        "physical_action": False,
        "changes_official_baseline": False,
    }


def step_candidate(ctx: dict[str, Any], step_id: str, action: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "action_ref": action["action_id"],
        "inputs": ctx["spans"],
        "output_contract": {"type": "measurement"},
        "preconditions": [],
        "stop_conditions": ["result recorded"],
        "effect_vector": {"effect_completeness_confirmed": True, "external_write": False},
        "state": "READY",
    }


async def begin(path: Path) -> tuple[Any, dict[str, Any]]:
    runtime, ctx = await setup(path)
    try:
        ctx["object_record"] = (
            await call(
                runtime,
                "object/read",
                "89a-setup-object",
                {"project_id": ctx["project"], "object_id": ctx["object"]},
            )
        )["object"]
        return runtime, ctx
    except BaseException:
        runtime.close()
        raise


async def add_actions(runtime: Any, ctx: dict[str, Any]) -> None:
    generated = await call(
        runtime,
        "action/generate",
        "89a-prepare-actions",
        {
            "project_id": ctx["project"],
            "object_id": ctx["object"],
            "decision_need": "Compare bounded alternatives",
            "evidence_scope": ctx["spans"],
        },
    )
    ctx["actions"] = generated["actions"]


async def add_portfolio(runtime: Any, ctx: dict[str, Any], stage: str) -> None:
    first, second = ctx["actions"][:2]
    portfolio = (
        await call(
            runtime,
            "action/portfolio/compose",
            "89a-prepare-portfolio",
            {
                "project_id": ctx["project"],
                "object_id": ctx["object"],
                "action_ids": [first["action_id"], second["action_id"]],
                "decision_need": "89a prepared choice",
                "portfolio_id": "portfolio:89a-prepared",
            },
        )
    )["portfolio"]
    for method, key, extra in (
        (
            "action/portfolio/evaluate",
            "89a-prepare-evaluate",
            {"evaluation_method": "89a method", "policy_criteria_ref": "policy:89a-prepared"},
        ),
        ("action/recommend", "89a-prepare-recommend", {"rationale": "89a prepared rationale"}),
    ):
        if stage == "composed":
            break
        portfolio = (
            await call(
                runtime,
                method,
                key,
                {
                    "project_id": ctx["project"],
                    "portfolio_id": portfolio["portfolio_id"],
                    **extra,
                },
            )
        )["portfolio"]
        if stage == "evaluated":
            break
    ctx["portfolio"] = portfolio


async def add_plan(runtime: Any, ctx: dict[str, Any]) -> None:
    first = ctx["actions"][0]
    ctx["plan"] = (
        await call(
            runtime,
            "action/plan/compose",
            "89a-prepare-plan",
            {
                "project_id": ctx["project"],
                "object_id": ctx["object"],
                "selected_action_refs": [first["action_id"]],
                "step_candidates": [step_candidate(ctx, "step:observation", first)],
                "dependency_edges": [],
                "plan_id": "plan:89a-prepared",
            },
        )
    )["plan"]


async def prepare(path: Path, kind: str) -> tuple[Any, dict[str, Any]]:
    runtime, ctx = await begin(path)
    try:
        await add_actions(runtime, ctx)
        if kind in {"evaluate", "recommend", "select"}:
            stage = {"evaluate": "composed", "recommend": "evaluated", "select": "recommended"}
            await add_portfolio(runtime, ctx, stage[kind])
        if kind in PLAN_KINDS:
            await add_plan(runtime, ctx)
        return runtime, ctx
    except BaseException:
        runtime.close()
        raise


def command_input(ctx: dict[str, Any], kind: str) -> tuple[str, dict[str, Any], str, str | None]:
    project = ctx["project"]
    base = {"project_id": project, "object_id": ctx["object"]}
    actions: list[dict[str, Any]] = ctx.get("actions", [])
    plan: dict[str, Any] = ctx.get("plan", {})
    portfolio: dict[str, Any] = ctx.get("portfolio", {})
    if kind == "revise":
        return (
            "action/revise",
            {
                "project_id": project,
                "action_id": actions[0]["action_id"],
                "expected_revision_digest": actions[0]["revision_digest"],
                "patch": {"expected_observation_or_change": {"description": "89a revised"}},
                "evidence_refs": ctx["spans"],
                "reason": "89a revise",
            },
            "action/updated",
            None,
        )
    if kind == "create":
        return (
            "action/create",
            {
                **base,
                "primary_purpose": "ANALYSIS_COMPUTATION",
                "specification": {
                    "description": "89a created candidate",
                    "expected_observation_or_change": {"description": "89a observation"},
                    "effect_completeness_confirmed": True,
                    "stop_conditions": ["89a stop"],
                    "observability": "89a receipt",
                    "effect_vector": effect_vector(),
                },
                "evidence_refs": ctx["spans"],
            },
            "action/created",
            None,
        )
    if kind == "compensation":
        return (
            "action/compensation/create",
            {
                "project_id": project,
                "caused_by_execution_attempt_ref": "attempt:89a",
                "observed_effect_refs": [],
                "intended_mitigation": "89a mitigation",
                "residual_effect_expectation": "89a residual",
                "evidence_refs": ctx["spans"],
            },
            "action/created",
            None,
        )
    if kind == "portfolio_compose":
        return (
            "action/portfolio/compose",
            {
                **base,
                "action_ids": [actions[0]["action_id"], actions[1]["action_id"]],
                "decision_need": "89a choice",
                "portfolio_id": "portfolio:89a",
            },
            "action/portfolioUpdated",
            None,
        )
    if kind == "evaluate":
        return (
            "action/portfolio/evaluate",
            {
                "project_id": project,
                "portfolio_id": portfolio["portfolio_id"],
                "evaluation_method": "89a method",
                "policy_criteria_ref": "policy:89a",
                "preference_inputs": [],
            },
            "action/evaluated",
            "policy_criteria_ref",
        )
    if kind == "recommend":
        return (
            "action/recommend",
            {
                "project_id": project,
                "portfolio_id": portfolio["portfolio_id"],
                "rationale": "89a rationale",
            },
            "action/recommended",
            "rationale",
        )
    if kind == "select":
        return (
            "action/select",
            {
                "project_id": project,
                "portfolio_id": portfolio["portfolio_id"],
                "action_id": actions[0]["action_id"],
                "decision_context": {"reason": "89a selection"},
                "actor_or_agent_ref": "human:local-user",
                "expected_portfolio_revision": portfolio["revision_digest"],
            },
            "action/selected",
            "decision_context",
        )
    if kind == "plan_compose":
        return (
            "action/plan/compose",
            {
                **base,
                "selected_action_refs": [actions[0]["action_id"]],
                "step_candidates": [step_candidate(ctx, "step:observation", actions[0])],
                "dependency_edges": [],
                "plan_id": "plan:89a",
            },
            "action/planComposed",
            None,
        )
    if kind == "merge":
        return (
            "action/merge/propose",
            {
                "project_id": project,
                "action_ids": [actions[0]["action_id"], actions[1]["action_id"]],
                "field_mapping": {"primary_purpose": "retain both"},
                "evidence_refs": ctx["spans"],
                "rationale": "89a preview only",
                "expected_revision_digests": [
                    actions[0]["revision_digest"],
                    actions[1]["revision_digest"],
                ],
            },
            "action/mergeProposed",
            None,
        )
    plan_base: dict[str, Any] = {"project_id": project, "plan_id": plan["plan_id"]}
    if kind == "plan_revise":
        steps: list[dict[str, Any]] = [dict(step) for step in plan["steps"]]
        steps[0]["stop_conditions"] = ["89a new checkpoint"]
        return (
            "action/plan/revise",
            {
                **plan_base,
                "expected_revision_digest": plan["revision_digest"],
                "patch": {"steps": steps},
                "evidence_refs": ctx["spans"],
                "reason": "89a revise plan",
            },
            "action/planUpdated",
            None,
        )
    if kind == "plan_revalidate":
        return (
            "action/plan/revalidate",
            {**plan_base, "trigger_reason": "89a trigger"},
            "action/planUpdated",
            None,
        )
    if kind == "step_add":
        return (
            "action/step/add",
            {
                **plan_base,
                "step_spec": step_candidate(ctx, "step:89a-added", actions[1]),
                "dependency_refs": [],
                "expected_plan_revision": plan["revision_digest"],
            },
            "action/planUpdated",
            None,
        )
    if kind == "step_revise":
        return (
            "action/step/revise",
            {
                **plan_base,
                "step_id": "step:observation",
                "patch": {"stop_conditions": ["89a revised stop"]},
                "evidence_refs": ctx["spans"],
                "reason": "89a revise step",
                "expected_plan_revision": plan["revision_digest"],
            },
            "action/planUpdated",
            None,
        )
    if kind == "impact":
        return (
            "action/impact/recalculate",
            {**plan_base, "trigger_refs": ctx["spans"]},
            "action/impactChanged",
            None,
        )
    assert kind == "policy"
    return (
        "action/policy/classify",
        {**plan_base, "policy_version": "policy:89a"},
        "action/policyChanged",
        None,
    )


def expected_reads(ctx: dict[str, Any], kind: str) -> tuple[str, ...]:
    """Source-defined top-level reads: see source-read-set-expectations.json (written first)."""

    actions = ctx.get("actions", [])
    first = revision(actions[0]["revision_digest"]) if actions else ""
    second = revision(actions[1]["revision_digest"]) if len(actions) > 1 else ""
    obj = revision(ctx["object_record"]["revision_digest"])
    reads: set[str]
    if kind == "revise":
        reads = {first}
    elif kind in {"create", "compensation"}:
        # evidence spans are read through ScopedArtifactLedger before the transaction (correction)
        reads = {
            obj,
            *(revision(item["revision_digest"]) for item in actions),
            *ctx["spans"],
        }
    elif kind == "portfolio_compose":
        reads = {obj, first, second}
    elif kind in {"evaluate", "recommend"}:
        reads = {revision(ctx["portfolio"]["revision_digest"]), first, second}
    elif kind == "select":
        reads = {revision(ctx["portfolio"]["revision_digest"])}
    elif kind == "plan_compose":
        reads = {obj, first}
    elif kind == "merge":
        reads = {first, second}
    else:
        reads = {revision(ctx["plan"]["revision_digest"])}
        if kind in {"plan_revise", "step_revise"}:
            reads |= set(ctx["spans"])
    return tuple(sorted(reads))


def install_fault(
    patch: Any, runtime: Any, ctx: dict[str, Any], command: Any, kind: str, point: str, before: Any
) -> tuple[dict[str, Any], Any]:
    state: dict[str, Any] = {
        "hits": [],
        "violations": [],
        "revisions": {},
        "participants": [],
        "selected_audit": None,
    }
    project = ctx["project"]
    _, _, wanted_event, extra_key = command_input(ctx, kind)

    def active() -> bool:
        op = current_operation.get()
        return op is not None and (op.project_id, op.method, op.idempotency_key) == (
            project,
            command.method,
            command.params.meta.idempotency_key,
        )

    def observe(connection: Any, table_name: str, identity: dict[str, Any]) -> None:
        table = metadata.tables[table_name]
        rows = list(
            connection.execute(
                select(table).where(*(table.c[k] == v for k, v in identity.items()))
            ).mappings()
        )
        external = snapshot(runtime.ledger.engine)
        external_changes = sorted(
            name for name in before if name not in BOOKKEEPING and external[name] != before[name]
        )
        staged: list[dict[str, Any]] = []
        for participant in state["participants"]:
            participant_table = metadata.tables[participant["table"]]
            found = list(
                connection.execute(
                    select(participant_table).where(
                        *(
                            participant_table.c[k] == v
                            for k, v in cast("dict[str, Any]", participant["identity"]).items()
                        )
                    )
                ).mappings()
            )
            staged.append({**participant, "rows": len(found)})
        state["hits"].append(
            {
                "point": point,
                "table": table_name,
                "identity": identity,
                "rows": len(rows),
                "participants": staged,
                "revisions": dict(state["revisions"]),
                "same_engine": connection.engine is runtime.ledger.engine,
                "in_transaction": connection.in_transaction(),
                "external_domain_changes": external_changes,
            }
        )
        if not (rows and connection.in_transaction()):
            state["violations"].append("target row is not staged inside the transaction")
        if connection.engine is not runtime.ledger.engine:
            state["violations"].append("target write used another engine")
        if not all(item["rows"] for item in staged):
            state["violations"].append("an earlier participant is not staged")
        if external_changes:
            state["violations"].append("intermediate domain publication: " + str(external_changes))

    def wrap(name: str, original: Any) -> Any:
        def call_original(instance: Any, *args: Any, **kwargs: Any) -> Any:
            selected = active()
            record = args[0]
            if name == "set_head":
                selected = selected and args[0] == project
                table = "working_heads"
                identity = {
                    "project_id": project,
                    "aggregate_key": args[1],
                    "revision_digest": args[2],
                }
            else:
                selected = selected and record.project_id == project
                table, field = WRITERS[name][1], WRITERS[name][2]
                identity = {"project_id": project, field: getattr(record, field)}
                if name == "insert_receipt":
                    selected = selected and bool(
                        set(record.subject_refs) & state["revisions"].keys()
                    )
            if (
                selected
                and name == "append_audit"
                and point == "proposal_insert"
                and record.event_type == wanted_event
            ):
                state["selected_audit"] = record.model_dump(mode="json")
            result = original(instance, *args, **kwargs)
            if not selected:
                return result
            if name == "insert_revision":
                state["revisions"][record.revision_id] = record.reason
            state["participants"].append({"table": table, "identity": identity})
            target = point == name
            if name == "append_audit":
                target = (
                    point == "append_audit"
                    and record.event_type == wanted_event
                    and extra_key is None
                ) or (
                    point == "extra_audit"
                    and record.event_type == wanted_event
                    and extra_key in record.payload
                )
            if target:
                with read_connection(runtime.ledger.engine) as connection:
                    if name in LEDGER_POINTS and connection is not instance._connection:
                        state["violations"].append("ledger write is not on the ambient connection")
                    observe(connection, table, identity)
                raise RuntimeError("89a post-write fault: " + kind + ":" + point)
            return result

        return call_original

    for name in (*LEDGER_POINTS, "add_action", "add_portfolio", "add_plan", "append_audit"):
        owner = SqliteLedgerTransaction if name in (*LEDGER_POINTS, "set_head") else None
        owner = owner or WRITERS[name][0]
        patch.setattr(owner, name, wrap(name, getattr(owner, name)))

    def after_sql(
        connection: Any, _cursor: Any, _statement: str, _params: Any, context: Any, _many: bool
    ) -> None:
        selected = state["selected_audit"]
        if (
            point != "proposal_insert"
            or not active()
            or selected is None
            or not context.isinsert
            or context.compiled is None
            or context.compiled.statement.table.name != "action_audit"
        ):
            return
        params = context.compiled_parameters
        if (
            len(params) != 1
            or params[0].get("audit_id") != selected["audit_id"]
            or params[0].get("project_id") != project
        ):
            return
        observe(
            connection,
            "action_audit",
            {"project_id": project, "audit_id": selected["audit_id"]},
        )
        raise RuntimeError("89a proposal INSERT before commit: " + kind)

    return state, after_sql


async def views(runtime: Any, ctx: dict[str, Any], phase: str) -> dict[str, Any]:
    base = {"project_id": ctx["project"]}
    result = {
        method: await call(runtime, method, phase + method, base)
        for method in ("action/list", "action/portfolio/list", "action/audit/read")
    }
    result["heads"] = dict(runtime.ledger.read_heads(ctx["project"]))
    return result


def publications(result: dict[str, Any]) -> list[tuple[dict[str, Any], str, str, dict[str, Any]]]:
    found: list[tuple[dict[str, Any], str, str, dict[str, Any]]] = []
    commits: list[dict[str, Any]] = result.get("commits") or [result.get("commit") or {}]
    if "actions" in result:
        return [
            (item, "action_id", "action_revision_id", commit)
            for item, commit in zip(result["actions"], result["commits"], strict=True)
        ]
    index = 0
    for field, id_key, revision_key in RECORDS:
        if field in result:
            found.append((result[field], id_key, revision_key, commits[index]))
            index += 1
    return found


def check_publication(runtime: Any, ctx: dict[str, Any], result: dict[str, Any]) -> None:
    for record, id_key, revision_key, commit in publications(result):
        heads = runtime.ledger.read_heads(ctx["project"])
        assert heads["ACTION:" + record[id_key]] == record["revision_digest"]
        assert record[revision_key] in commit["receipt"]["subject_refs"]


async def change(
    runtime: Any, ctx: dict[str, Any], method: str, key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    command = request(method, key, payload)
    result = value(await runtime.bus.dispatch(command))
    after = snapshot(runtime.ledger.engine)
    assert value(await runtime.bus.dispatch(command)) == result
    assert snapshot(runtime.ledger.engine) == after
    check_publication(runtime, ctx, result)
    return result


async def test_decision_chain_control(tmp_path: Path) -> None:
    runtime, ctx = await begin(tmp_path)
    project = ctx["project"]
    try:
        await add_actions(runtime, ctx)
        first, second = ctx["actions"][:2]
        for item in ctx["actions"]:
            assert (
                item["revision_digest"]
                == runtime.ledger.read_heads(project)["ACTION:" + item["action_id"]]
            )
        created = await change(
            runtime, ctx, "action/create", "89a-control-create", command_input(ctx, "create")[1]
        )
        revised = await change(
            runtime,
            ctx,
            "action/revise",
            "89a-control-revise",
            command_input(ctx, "revise")[1],
        )
        assert revised["action"]["supersedes_revision_digest"] == first["revision_digest"]
        original = await call(
            runtime,
            "action/read",
            "89a-control-original",
            {
                "project_id": project,
                "action_id": first["action_id"],
                "revision_digest": first["revision_digest"],
            },
        )
        assert original["action"] == first
        ctx["actions"][0] = revised["action"]
        composed = await change(
            runtime,
            ctx,
            "action/portfolio/compose",
            "89a-control-portfolio",
            command_input(ctx, "portfolio_compose")[1],
        )
        ctx["portfolio"] = composed["portfolio"]
        for method, key, extra in (
            (
                "action/portfolio/evaluate",
                "89a-control-evaluate",
                {"evaluation_method": "89a method", "policy_criteria_ref": "policy:89a"},
            ),
            ("action/recommend", "89a-control-recommend", {"rationale": "89a rationale"}),
        ):
            result = await change(
                runtime,
                ctx,
                method,
                key,
                {"project_id": project, "portfolio_id": ctx["portfolio"]["portfolio_id"], **extra},
            )
            ctx["portfolio"] = result["portfolio"]
        selected = await change(
            runtime,
            ctx,
            "action/select",
            "89a-control-select",
            command_input(ctx, "select")[1],
        )
        assert selected["action"]["proposal_state"] == "SELECTED"
        assert selected["authorization_implied"] is False
        composed_plan = await change(
            runtime,
            ctx,
            "action/plan/compose",
            "89a-control-plan",
            command_input(ctx, "plan_compose")[1],
        )
        ctx["plan"] = composed_plan["plan"]
        for kind in (
            "step_add",
            "step_revise",
            "plan_revalidate",
            "impact",
            "policy",
            "plan_revise",
        ):
            method, payload, _, _ = command_input(ctx, kind)
            result = await change(runtime, ctx, method, "89a-control-" + kind, payload)
            ctx["plan"] = result["plan"]
        assert any(step["step_id"] == "step:89a-added" for step in ctx["plan"]["steps"])
        compensation = await change(
            runtime,
            ctx,
            "action/compensation/create",
            "89a-control-compensation",
            command_input(ctx, "compensation")[1],
        )
        assert compensation["exact_rollback_claimed"] is False
        merge_ctx = {**ctx, "actions": [ctx["actions"][1], created["action"]]}
        method, payload, _, _ = command_input(merge_ctx, "merge")
        before_merge = runtime.ledger.read_heads(project)
        proposal = await change(runtime, ctx, method, "89a-control-merge", payload)
        assert proposal["merge_proposal"]["applied"] is False
        assert runtime.ledger.read_heads(project) == before_merge
        assert second["action_id"] in proposal["merge_proposal"]["action_ids"]
        expected = await views(runtime, ctx, "89a-live")
        persisted = snapshot(runtime.ledger.engine)
        save("control-decision-chain", {"views": expected, "heads": expected["heads"]})
        assert expected["action/audit/read"]["records"]
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert snapshot(reopened.ledger.engine) == persisted
        assert await views(reopened, ctx, "89a-reopen") == expected
    finally:
        reopened.close()


@pytest.mark.parametrize("kind,point", CASES)
async def test_action_post_write_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, point: str
) -> None:
    runtime, ctx = await prepare(tmp_path, kind)
    key = "89a-fault-" + kind + "-" + point
    method, payload, _, _ = command_input(ctx, kind)
    command = request(method, key, payload)
    reads = expected_reads(ctx, kind)
    views_before = await views(runtime, ctx, "89a-before")
    before = snapshot(runtime.ledger.engine)
    try:
        registry = cast(Any, vars(runtime.bus)["_registry"])
        actual = registry.resolve(method)
        binding = actual.__module__ + "." + actual.__qualname__
        assert binding.startswith("thoth.application.commands.actions_full.ActionHandlers.")
        with monkeypatch.context() as patch:
            state, callback = install_fault(patch, runtime, ctx, command, kind, point, before)
            if point == "proposal_insert":
                event.listen(runtime.ledger.engine, "after_cursor_execute", callback)
            try:
                failed = await runtime.bus.dispatch(command)
            finally:
                if point == "proposal_insert":
                    event.remove(runtime.ledger.engine, "after_cursor_execute", callback)
        after = snapshot(runtime.ledger.engine)
        operation = next(
            json.loads(row)
            for row in after["operations"]
            if json.loads(row)["idempotency_key"] == key
        )
        binding_row = next(
            json.loads(row)
            for row in after["operation_resource_bindings"]
            if json.loads(row)["operation_id"] == operation["operation_id"]
        )
        actual_reads = sorted(
            (use["resource_ref"], use["capability"])
            for use in json.loads(binding_row["content_json"])["resource_uses"]
        )
        evidence: dict[str, Any] = {
            "case": [kind, point],
            "request": command.model_dump(mode="json"),
            "error": failed.model_dump(mode="json").get("error"),
            "expected_reads": reads,
            "observed_resource_uses": actual_reads,
            "actual_callable": binding,
            "fault": state,
            "changed_tables": sorted(name for name in before if before[name] != after[name]),
        }
        assert len(state["hits"]) == 1, "HOLD_CONTRACT_MISMATCH: expected target hit1"
        assert not state["violations"], ("HOLD_DEFECT", state["violations"])
        assert failed.error is not None, "HOLD_DEFECT: post-write failure reported success"
        assert all(before[t] == after[t] for t in before if t not in BOOKKEEPING), (
            "HOLD_DEFECT: failed mutation published domain rows"
        )
        assert actual_reads == [(ref, "READ") for ref in reads], (
            "HOLD_CONTRACT_MISMATCH: source-defined read-set differs",
            reads,
            actual_reads,
        )
        allowed = failed_command_allowances(runtime.ledger.engine, command, expected_reads=reads)
        assert_phase_delta(before, after, allowed)
        participants = {item["table"] for item in state["hits"][0]["participants"]}
        if kind == "select":
            assert {"action_records", "action_portfolios", "semantic_revisions"} <= participants
        if point == "extra_audit" or kind in {"portfolio_compose", "evaluate", "recommend"}:
            assert "action_portfolios" in participants
        if kind.startswith("plan") or kind in {"step_add", "step_revise", "impact", "policy"}:
            assert "action_plans" in participants
        if kind == "merge":
            assert state["selected_audit"]["payload"]["applied"] is False
        save(key, evidence)
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        restarted = snapshot(reopened.ledger.engine)
        assert restarted == after
        replay = await reopened.bus.dispatch(command)
        assert replay.error == failed.error, "HOLD_DEFECT: replay changed the failed result"
        assert snapshot(reopened.ledger.engine) == after, (
            "HOLD_DEFECT: failed replay changed stored state"
        )
        assert await views(reopened, ctx, "89a-preserved") == views_before
        recovered = value(await reopened.bus.dispatch(request(method, key + "-retry", payload)))
        if kind == "merge":
            assert recovered["merge_proposal"]["applied"] is False
            assert dict(reopened.ledger.read_heads(ctx["project"])) == views_before["heads"]
        else:
            check_publication(reopened, ctx, recovered)
        save(key, {**evidence, "verdict": "PASS", "replay_exact": True})
    finally:
        reopened.close()
