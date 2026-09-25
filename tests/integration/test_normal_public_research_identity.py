from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from pydantic import BaseModel
from tests.integration.storage_coverage_helpers import domain_snapshot, request, value
from tests.integration.test_a02_autonomous_acquisition import (
    A02Connector,
    DynamicA02Model,
    StaticModelResolver,
    prepare_thread,
)
from tests.integration.test_research_identity_migration import seed_legacy
from tests.unit.test_research_projection import make_hypothesis

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.storage.action import SqliteActionStore
from thoth.application.services.research_identity_service import (
    ResearchContext,
    ResearchIdentityService,
    ResearchOwnershipBatch,
)
from thoth.apps.runtime import create_runtime
from thoth.domain.action import ActionPlan
from thoth.domain.actor import ActorRef
from thoth.domain.canonical import model_digest
from thoth.domain.enums import CausalLocus, ModelRole, PortfolioStatus
from thoth.domain.hypothesis import HypothesisPortfolio
from thoth.domain.model import ModelRequest, ModelResult
from thoth.ports.model import ModelPort

TModel = TypeVar("TModel", bound=BaseModel)


@pytest.mark.asyncio
async def test_normal_cycle_does_not_replace_full_criterion_owner_with_its_view(
    tmp_path: Path,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "criterion-owner-cycle",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        listed = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/list",
                    "criterion-owner-list",
                    {
                        "project_id": project,
                    },
                )
            )
        )
        assert listed["criteria"]
        for contract in listed["criteria"]:
            head = runtime.ledger.read_heads(project)[f"CRITERION:{contract['criterion_id']}"]
            assert head == contract["revision_digest"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_normal_action_plan_is_readable_through_full_public_owner(tmp_path: Path) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        cycle = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "action-first",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        plan = cycle["action_plan"]
        expected = {item["action_id"] for item in plan["alternatives"]}
        listed = value(
            await runtime.bus.dispatch(
                request(
                    "action/list",
                    "action-list",
                    {
                        "project_id": project,
                        "object_id": plan["object_id"],
                    },
                )
            )
        )
        assert {item["action_id"] for item in listed["actions"]} == expected
        public_plan = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/read",
                    "action-plan-read",
                    {
                        "project_id": project,
                        "plan_id": plan["plan_id"],
                    },
                )
            )
        )["plan"]
        assert set(public_plan["selected_action_refs"]) == set(plan["frontier"])
        assert (
            public_plan["revision_digest"]
            == runtime.ledger.read_heads(project)[f"ACTION:{plan['plan_id']}"]
        )
        for identifier in expected:
            record = value(
                await runtime.bus.dispatch(
                    request(
                        "action/read",
                        identifier,
                        {
                            "project_id": project,
                            "action_id": identifier,
                        },
                    )
                )
            )["action"]
            assert record["object_id"] == plan["object_id"]
            assert (
                record["revision_digest"]
                == runtime.ledger.read_heads(project)[f"ACTION:{identifier}"]
            )
    finally:
        runtime.close()


def capture_contexts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    original = DynamicA02Model.structured

    async def observe(
        self: DynamicA02Model, model_request: ModelRequest[TModel]
    ) -> ModelResult[TModel]:
        if model_request.role == ModelRole.HYPOTHESIS_GENERATOR:
            contexts.append(model_request.context_pack.model_dump(mode="json"))
        return await original(self, model_request)

    monkeypatch.setattr(DynamicA02Model, "structured", observe)
    return contexts


@pytest.mark.asyncio
async def test_unknown_legacy_is_held_without_empty_portfolio_or_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = capture_contexts(monkeypatch)
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        revision = seed_legacy(
            runtime.ledger, project, "legacy:opaque", {"unknown_contract": "retained"}
        )
        snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snapshot is not None
        saved = snapshot.model_dump(mode="json")
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                "legacy-held",
                {
                    "project_id": project,
                    "thread_id": f"thread:{project}",
                },
            )
        )
        assert response.error is not None and "LEGACY_SCHEMA_UNRESOLVED" in response.error.message
        assert contexts == []
        current = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert current is not None and current.model_dump(mode="json") == saved
        assert (
            runtime.ledger.read_heads(project)["HYPOTHESIS:legacy:opaque"]
            == revision.revision_digest
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_foreign_legacy_embedded_identity_cannot_be_reused_by_model(tmp_path: Path) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "foreign-project",
                    {
                        "project_id": "project:foreign",
                        "name": "Foreign fixture",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        legacy = HypothesisPortfolio(
            portfolio_id="portfolio:foreign",
            object_id="object:identity",
            hypotheses=(
                make_hypothesis("hypothesis:a02:data", CausalLocus.INPUT_MATERIAL_DATA),
                make_hypothesis(
                    "hypothesis:foreign:second", CausalLocus.METHOD_DESIGN_IMPLEMENTATION
                ),
            ),
            status=PortfolioStatus.TESTABLE,
            generated_from_head_set="0" * 64,
        )
        revision = seed_legacy(
            runtime.ledger, "project:foreign", legacy.portfolio_id, legacy.model_dump(mode="python")
        )
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                "foreign-id-collision",
                {
                    "project_id": project,
                    "thread_id": f"thread:{project}",
                },
            )
        )
        assert (
            response.error is not None
            and "RESEARCH_ENTITY_SCOPE_OR_FAMILY_MISMATCH" in response.error.message
        )
        assert "project:foreign" not in response.error.message
        assert not any(
            key.startswith(("HYPOTHESIS:", "ACTION:")) for key in runtime.ledger.read_heads(project)
        )
        assert (
            runtime.ledger.read_heads("project:foreign")["HYPOTHESIS:portfolio:foreign"]
            == revision.revision_digest
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["other_object", "duplicate_id"])
async def test_invalid_candidate_identity_writes_no_research_heads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    original = DynamicA02Model.structured

    async def invalid(
        self: DynamicA02Model, model_request: ModelRequest[TModel]
    ) -> ModelResult[TModel]:
        result = await original(self, model_request)
        if model_request.role != ModelRole.HYPOTHESIS_GENERATOR:
            return result
        payload = result.output.model_dump(mode="python")
        if mutation == "other_object":
            payload["hypotheses"][0]["object_id"] = "object:foreign"
        else:
            payload["hypotheses"][1]["hypothesis_id"] = payload["hypotheses"][0]["hypothesis_id"]
        output = model_request.output_model.model_validate(payload)
        return replace(
            result,
            output=output,
            output_digest=model_digest("A02_MODEL_OUTPUT", output, schema_version="1.0.0"),
        )

    monkeypatch.setattr(DynamicA02Model, "structured", invalid)
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                "invalid-identity",
                {
                    "project_id": project,
                    "thread_id": f"thread:{project}",
                },
            )
        )
        assert response.error is not None
        heads = runtime.ledger.read_heads(project)
        assert not any(key.startswith(("HYPOTHESIS:", "ACTION:")) for key in heads)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_public_action_draft_is_preserved_without_polluting_automatic_frontier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = capture_contexts(monkeypatch)
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read",
                    "action-draft-thread",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        draft = value(
            await runtime.bus.dispatch(
                request(
                    "action/create",
                    "action-public-draft",
                    {
                        "project_id": project,
                        "object_id": thread["current_object_ids"][0],
                        "portfolio_id": "portfolio:public-actions",
                        "primary_purpose": "ANALYSIS_COMPUTATION",
                        "specification": {"description": "An incomplete public action"},
                        "evidence_refs": [],
                    },
                )
            )
        )["action"]
        assert draft["risk_tier"] == "R3"
        cycle = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "action-after-public",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        assert any(
            item["action_id"] == draft["action_id"] for item in contexts[-1]["canonical_actions"]
        )
        portfolio = value(
            await runtime.bus.dispatch(
                request(
                    "action/portfolio/read",
                    "action-draft-portfolio",
                    {
                        "project_id": project,
                        "portfolio_id": "portfolio:public-actions",
                    },
                )
            )
        )["portfolio"]
        assert draft["action_id"] in portfolio["action_refs"]
        plan = value(
            await runtime.bus.dispatch(
                request(
                    "action/plan/read",
                    "action-draft-plan",
                    {
                        "project_id": project,
                        "plan_id": cycle["action_plan"]["plan_id"],
                    },
                )
            )
        )["plan"]
        assert draft["action_id"] not in plan["auto_executable_frontier"]
        assert plan["required_role_union"] == []
        preserved = value(
            await runtime.bus.dispatch(
                request(
                    "action/read",
                    "action-draft-preserved",
                    {
                        "project_id": project,
                        "action_id": draft["action_id"],
                    },
                )
            )
        )["action"]
        assert preserved["revision_digest"] == draft["revision_digest"]
        assert preserved["required_roles"] == draft["required_roles"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_other_object_full_records_are_not_mixed_into_normal_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = capture_contexts(monkeypatch)
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        other = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "other-thread",
                    {
                        "project_id": project,
                        "thread_id": "thread:other-object",
                        "problem": "A distinct research question",
                        "scope": {"workstream": "other"},
                    },
                )
            )
        )
        draft = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/create",
                    "other-object-hypothesis",
                    {
                        "project_id": project,
                        "object_id": other["current_object_ids"][0],
                        "portfolio_id": "portfolio:other-object",
                        "statement": "Other object candidate",
                        "primary_intent": "EXPLORATORY",
                        "evidence_basis": "UNRESOLVED",
                        "scope": {"question": "other"},
                        "evidence_refs": [],
                    },
                )
            )
        )["hypothesis"]
        cycle = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "original-object-cycle",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        assert cycle["portfolio"]["object_id"] != draft["object_id"]
        assert all(
            item["object_id"] != draft["object_id"] for item in contexts[-1]["canonical_hypotheses"]
        )
        preserved = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "other-object-preserved",
                    {
                        "project_id": project,
                        "hypothesis_id": draft["hypothesis_id"],
                    },
                )
            )
        )["hypothesis"]
        assert preserved["revision_digest"] == draft["revision_digest"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_admission_fault_rolls_back_all_research_owners_and_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    snapshots: list[dict[str, tuple[str, ...]]] = []
    original_stage = ResearchIdentityService.stage_cycle
    original_add = SqliteActionStore.add_action

    def stage(
        self: ResearchIdentityService,
        portfolio: HypothesisPortfolio,
        plan: ActionPlan,
        *,
        actor: ActorRef,
        context: ResearchContext,
    ) -> ResearchOwnershipBatch:
        batch = original_stage(self, portfolio, plan, actor=actor, context=context)
        snapshots.append(domain_snapshot(runtime.ledger.engine))
        return batch

    def fail(self: SqliteActionStore, record: Any) -> None:
        original_add(self, record)
        raise RuntimeError("N02_INJECTED_ACTION_PROJECTION_FAULT")

    monkeypatch.setattr(ResearchIdentityService, "stage_cycle", stage)
    monkeypatch.setattr(SqliteActionStore, "add_action", fail)
    try:
        response = await runtime.bus.dispatch(
            request(
                "thread/input",
                "research-fault",
                {
                    "project_id": project,
                    "thread_id": f"thread:{project}",
                },
            )
        )
        assert response.error is not None and snapshots
        after = domain_snapshot(runtime.ledger.engine)
        names = {
            "entity_snapshots",
            "semantic_revisions",
            "revision_parents",
            "working_heads",
            "receipts",
            "hypothesis_records",
            "hypothesis_portfolios",
            "hypothesis_audit",
            "research_identities",
            "action_records",
            "action_portfolios",
            "action_plans",
            "action_audit",
            "dependency_states",
        }
        names.update(name for name in after if name.startswith("memory_"))
        assert {name: after[name] for name in names} == {name: snapshots[0][name] for name in names}
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_concurrent_public_edit_survives_stale_normal_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "cas-first",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        identifier = first["portfolio"]["hypotheses"][0]["hypothesis_id"]
        edited: list[dict[str, Any]] = []
        original = DynamicA02Model.structured

        async def race(
            self: DynamicA02Model, model_request: ModelRequest[TModel]
        ) -> ModelResult[TModel]:
            if model_request.role == ModelRole.HYPOTHESIS_GENERATOR and not edited:
                current = value(
                    await runtime.bus.dispatch(
                        request(
                            "hypothesis/read",
                            "cas-read",
                            {
                                "project_id": project,
                                "hypothesis_id": identifier,
                            },
                        )
                    )
                )["hypothesis"]
                edited.append(
                    value(
                        await runtime.bus.dispatch(
                            request(
                                "hypothesis/revise",
                                "cas-edit",
                                {
                                    "project_id": project,
                                    "hypothesis_id": identifier,
                                    "expected_revision_digest": current["revision_digest"],
                                    "patch": {"statement": "Concurrent public correction"},
                                    "evidence_refs": current["evidence_refs"],
                                    "reason": "concurrent public update",
                                },
                            )
                        )
                    )["hypothesis"]
                )
            return await original(self, model_request)

        monkeypatch.setattr(DynamicA02Model, "structured", race)
        result = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "cas-second",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        assert result["terminal_state"] == "BRANCHED"
        assert result["downstream_after_branch_executed"] is False
        current = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "cas-current",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                    },
                )
            )
        )["hypothesis"]
        assert current["statement"] == "Concurrent public correction"
        assert current["revision_digest"] == edited[0]["revision_digest"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_public_revision_is_next_model_basis_and_retained_in_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = capture_contexts(monkeypatch)
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "basis-first",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        identifier = first["portfolio"]["hypotheses"][0]["hypothesis_id"]
        original = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "basis-read",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                    },
                )
            )
        )["hypothesis"]
        edited = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/revise",
                    "basis-edit",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                        "expected_revision_digest": original["revision_digest"],
                        "patch": {"statement": "Publicly corrected working hypothesis"},
                        "evidence_refs": original["evidence_refs"],
                        "reason": "explicit public correction",
                    },
                )
            )
        )["hypothesis"]
        contexts.clear()
        second = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "basis-second",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        supplied = next(
            item
            for item in contexts[-1]["canonical_hypotheses"]
            if item["hypothesis_id"] == identifier
        )
        assert supplied["statement"] == edited["statement"]
        assert supplied["revision_digest"] == edited["revision_digest"]
        current = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "basis-current",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                    },
                )
            )
        )["hypothesis"]
        candidate = next(
            item
            for item in second["portfolio"]["hypotheses"]
            if item["hypothesis_id"] == identifier
        )
        assert current["statement"] == candidate["statement"]
        assert current["supersedes_revision_digest"] == edited["revision_digest"]
        history = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "basis-history",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                        "revision_digest": edited["revision_digest"],
                    },
                )
            )
        )["hypothesis"]
        assert history["statement"] == "Publicly corrected working hypothesis"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_restore_and_reopen_use_owner_revision_without_rewriting_snapshot(
    tmp_path: Path,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        from tests.integration.test_restore_apply_atomicity import handler

        handler(runtime)
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "restore-first",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        identifier = first["portfolio"]["hypotheses"][0]["hypothesis_id"]
        original = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "restore-read",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                    },
                )
            )
        )["hypothesis"]
        revision = runtime.ledger.read_revision_by_digest(project, original["revision_digest"])
        assert revision is not None
        snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snapshot is not None
        saved = snapshot.model_dump(mode="json")
        edited = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/revise",
                    "restore-edit",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                        "expected_revision_digest": original["revision_digest"],
                        "patch": {"statement": "A later different statement"},
                        "evidence_refs": original["evidence_refs"],
                        "reason": "later revision before restore",
                    },
                )
            )
        )["hypothesis"]
        value(
            await runtime.bus.dispatch(
                request(
                    "revision/restore",
                    "restore-as-new",
                    {
                        "project_id": project,
                        "entity_type": "HYPOTHESIS",
                        "entity_id": identifier,
                        "selected_revision_id": revision.revision_id,
                        "expected_current_head": edited["revision_digest"],
                        "reason": "restore prior hypothesis as a new revision",
                        "actor_id": "human:test-owner",
                        "actor_role": "project-owner",
                    },
                )
            )
        )
        restored_head = runtime.ledger.read_heads(project)[f"HYPOTHESIS:{identifier}"]
        restored = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "restore-current",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                    },
                )
            )
        )["hypothesis"]
        assert restored["statement"] == original["statement"]
        assert restored["revision_digest"] == restored_head
        assert restored_head not in {original["revision_digest"], edited["revision_digest"]}
        unchanged = runtime.ledger.read_snapshot(revision.snapshot_id)
        assert unchanged is not None and unchanged.model_dump(mode="json") == saved
    finally:
        runtime.close()
    reopened = create_runtime(
        tmp_path / "allowed",
        connector_registry=ConnectorRegistry((A02Connector(),)),
        model_resolver=StaticModelResolver(cast(ModelPort, DynamicA02Model())),
    )
    try:
        readback = value(
            await reopened.bus.dispatch(
                request(
                    "hypothesis/read",
                    "reopen-current",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                    },
                )
            )
        )["hypothesis"]
        assert readback["revision_digest"] == restored_head
        value(
            await reopened.bus.dispatch(
                request(
                    "thread/input",
                    "reopen-next-cycle",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        current = value(
            await reopened.bus.dispatch(
                request(
                    "hypothesis/read",
                    "reopen-next-record",
                    {
                        "project_id": project,
                        "hypothesis_id": identifier,
                    },
                )
            )
        )["hypothesis"]
        assert current["supersedes_revision_digest"] == restored_head
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_normal_cycle_exposes_same_hypotheses_and_portfolio_in_public_api(
    tmp_path: Path,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        cycle = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "normal-cycle",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        portfolio = cycle["portfolio"]
        listed = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/list",
                    "public-list",
                    {
                        "project_id": project,
                        "object_id": portfolio["object_id"],
                    },
                )
            )
        )
        expected = {item["hypothesis_id"] for item in portfolio["hypotheses"]}
        assert {item["hypothesis_id"] for item in listed["hypotheses"]} == expected
        public_portfolio = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/portfolio/read",
                    "public-portfolio",
                    {
                        "project_id": project,
                        "portfolio_id": portfolio["portfolio_id"],
                    },
                )
            )
        )["portfolio"]
        assert set(public_portfolio["hypothesis_refs"]) == expected
        heads = runtime.ledger.read_heads(project)
        assert (
            public_portfolio["revision_digest"] == heads[f"HYPOTHESIS:{portfolio['portfolio_id']}"]
        )
        for identifier in expected:
            record = value(
                await runtime.bus.dispatch(
                    request(
                        "hypothesis/read",
                        identifier,
                        {
                            "project_id": project,
                            "hypothesis_id": identifier,
                        },
                    )
                )
            )["hypothesis"]
            assert record["object_id"] == portfolio["object_id"]
            assert record["portfolio_id"] == portfolio["portfolio_id"]
            assert record["revision_digest"] == heads[f"HYPOTHESIS:{identifier}"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_public_draft_survives_normal_cycle_and_is_visible_to_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts: list[dict[str, Any]] = []
    original = DynamicA02Model.structured

    async def observe(
        self: DynamicA02Model, model_request: ModelRequest[TModel]
    ) -> ModelResult[TModel]:
        if model_request.role == ModelRole.HYPOTHESIS_GENERATOR:
            contexts.append(model_request.context_pack.model_dump(mode="json"))
        return await original(self, model_request)

    monkeypatch.setattr(DynamicA02Model, "structured", observe)
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read",
                    "read-thread",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                    },
                )
            )
        )
        spans = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/list",
                    "read-evidence",
                    {
                        "project_id": project,
                    },
                )
            )
        )["spans"]
        draft = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/create",
                    "public-create",
                    {
                        "project_id": project,
                        "object_id": thread["current_object_ids"][0],
                        "portfolio_id": "portfolio:a02",
                        "statement": "Dataset version may explain the result",
                        "primary_intent": "DIAGNOSTIC_CAUSAL",
                        "evidence_basis": "SOURCE",
                        "scope": {"question": "dataset version"},
                        "evidence_refs": [item["span_id"] for item in spans],
                    },
                )
            )
        )["hypothesis"]
        cycle = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "after-public-create",
                    {
                        "project_id": project,
                        "thread_id": f"thread:{project}",
                        "instruction": (
                            "Reconsider the current hypotheses and preserve incomplete drafts."
                        ),
                    },
                )
            )
        )
        assert contexts
        supplied = contexts[-1].get("canonical_hypotheses", [])
        assert any(
            item["hypothesis_id"] == draft["hypothesis_id"]
            and item["revision_digest"] == draft["revision_digest"]
            for item in supplied
        )
        current = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "read-original-draft",
                    {
                        "project_id": project,
                        "hypothesis_id": draft["hypothesis_id"],
                    },
                )
            )
        )["hypothesis"]
        assert current["statement"] == draft["statement"]
        portfolio = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/portfolio/read",
                    "read-current-portfolio",
                    {
                        "project_id": project,
                        "portfolio_id": cycle["portfolio"]["portfolio_id"],
                    },
                )
            )
        )["portfolio"]
        assert draft["hypothesis_id"] in portfolio["hypothesis_refs"]
    finally:
        runtime.close()
