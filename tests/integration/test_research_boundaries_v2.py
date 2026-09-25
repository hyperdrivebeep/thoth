"""Adversarial states via public requests; controlled transport is explicitly local."""

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a02_autonomous_acquisition import A02Connector, policy_payload
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.connectors import ConnectorRegistry
from thoth.application.services.research_boundary import RequestBoundary
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.ports.model import ModelExecutionHold
from thoth.protocol.bus import DispatchTicket


async def finished(runtime: AppRuntime, question: str = "LAB-42 조건을 설명해줘"):
    admitted = value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "start",
                {"project_id": "p", "problem": question, "contract_version": 2},
            )
        )
    )
    await runtime.bus.drain()
    operation = runtime.bus.read_operation(str(admitted["operation_id"]))
    assert operation is not None and operation.state.value == "SUCCEEDED", operation
    status = value(
        await runtime.bus.dispatch(
            request(
                "thread/read", "status", {"project_id": "p", "thread_id": admitted["thread_id"]}
            )
        )
    )
    return admitted, status


@pytest.mark.asyncio
async def test_single_predictive_draft_shares_full_identity_and_is_not_promoted(tmp_path: Path):
    model = ControlledResearchModel(one=True)
    runtime = await setup(tmp_path, model)
    try:
        _, status = await finished(runtime)
        cycle = status["current_result"]["result"]
        hypothesis = cycle["portfolio"]["hypotheses"][0]
        assert hypothesis["primary_locus"] is None
        assert hypothesis["status"] == "DRAFT"
        full = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/read",
                    "full",
                    {"project_id": "p", "hypothesis_id": hypothesis["hypothesis_id"]},
                )
            )
        )
        assert full["hypothesis"]["development_stage"] == "DRAFT"
        assert full["hypothesis"]["causal_profile"]["applicability"] == "NOT_APPLICABLE"
        assert all(
            a["hypothesis_ids"] == [hypothesis["hypothesis_id"]]
            for a in cycle["action_plan"]["alternatives"]
        )
        assert any(c.role == ModelRole.HYPOTHESIS_REVIEWER for c in model.calls)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_model_na_cannot_waive_bound_but_reviewed_research_check_can_be_na(tmp_path: Path):
    runtime = await setup(tmp_path, ControlledResearchModel(na=True))
    try:
        _, status = await finished(runtime)
        result = status["current_result"]["result"]
        assessments = result["coverage"]["assessments"]
        assert all(
            a["resolution"] == "UNRESOLVED"
            for a in assessments
            if a["requirement_id"].startswith("bound:")
        )
        assert (
            next(a for a in assessments if a["requirement_id"] == "check:0")["resolution"]
            == "NOT_APPLICABLE"
        )
        assert result["coverage"]["web_decision"] != "SKIPPED_SUFFICIENT"
        assert result["coverage"]["gates"]["answer"] == "HOLD"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_permitted_public_discovery_is_followed_by_reader_and_parser(tmp_path: Path):
    connector = A02Connector()
    connector.capability = connector.capability.model_copy(
        update={"source_kind": "REST", "egress_class": "ALLOWLISTED_EXTERNAL"}
    )
    runtime = await setup(
        tmp_path,
        ControlledResearchModel(discover=True),
        source=False,
        connector_registry=ConnectorRegistry((connector,)),
    )
    try:
        policy = policy_payload(allow_connector=True)
        policy["connector_allowed_egress_classes"] = ["NONE", "ALLOWLISTED_EXTERNAL"]
        # Free-form research questions retain INTERNAL query classification.
        policy["max_query_egress_security_class"] = "INTERNAL"
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    "policy",
                    {"project_id": "p", "expected_revision": 0, "payload": policy},
                )
            )
        )
        _, status = await finished(runtime, "공개 자료에서 최신 데이터 조건을 확인해줘")
        result = status["current_result"]["result"]
        assert connector.discover_count >= 1 and connector.fetch_count >= 1
        assert result["discovery"]["acquired_count"] == 1
        assert result["coverage"]["web_decision"] == "SEARCHED_BOUNDED"
        sources = value(
            await runtime.bus.dispatch(request("evidence/list", "sources", {"project_id": "p"}))
        )
        assert sources["evidence"]
        # Acquisition has not magically certified the source's date or scientific truth.
        assert any(s["cutoff_state"] == "UNKNOWN_TIME" for s in sources["evidence"])
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_detached_source_does_not_remain_a_current_answer(tmp_path: Path):
    runtime = await setup(tmp_path, ControlledResearchModel())
    try:
        admitted, _ = await finished(runtime)
        project = value(
            await runtime.bus.dispatch(
                request("project/source/list", "source-list", {"project_id": "p"})
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/disconnect",
                    "detach",
                    {
                        "project_id": "p",
                        "binding_id": project["bindings"][0]["binding_id"],
                        "mode": "DETACH",
                    },
                )
            )
        )
        current = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read",
                    "after-detach",
                    {"project_id": "p", "thread_id": admitted["thread_id"]},
                )
            )
        )
        assert current["current_result"] is None
        assert current["previous_result"] is not None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_explicit_cancel_stops_dispatch_without_terminal_publication(tmp_path: Path):
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Stop this investigation",
                        "contract_version": 2,
                    },
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 10)
        value(
            await runtime.bus.dispatch(
                request(
                    "operation/cancel",
                    "cancel",
                    {"project_id": "p", "operation_id": admitted["operation_id"]},
                )
            )
        )
        model.release.set()
        await runtime.bus.drain()
        operation = runtime.bus.read_operation(str(admitted["operation_id"]))
        assert operation is not None and operation.state.value == "CANCELLED"
        assert len(model.calls) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_claim_only_restart_reenters_admission_once(tmp_path: Path):
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    original = request(
        "thread/start",
        "claim-crash",
        {"project_id": "p", "problem": "A durable question", "contract_version": 2},
    )
    ticket = runtime.bus.claim(original)
    assert isinstance(ticket, DispatchTicket)
    runtime.close()
    recovered = create_runtime(
        tmp_path, model_resolver=model, resource_scope_policy=fixture_scope_policy()
    )
    try:
        admitted = value(await recovered.bus.dispatch(original))
        assert admitted["operation_id"] == ticket.operation.operation_id
        await recovered.bus.drain()
        threads = value(
            await recovered.bus.dispatch(request("thread/list", "threads", {"project_id": "p"}))
        )
        assert len(threads["threads"]) == 1
    finally:
        recovered.close()


class UnavailableModel(ControlledResearchModel):
    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        raise ModelExecutionHold("CONTROLLED_TRANSPORT_UNSUPPORTED")


@pytest.mark.asyncio
async def test_replace_cas_and_history_preserve_superseded_input(tmp_path: Path):
    model = ControlledResearchModel(wait=True)
    runtime = await setup(tmp_path, model)
    try:
        first = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {"project_id": "p", "problem": "Original question", "contract_version": 2},
                )
            )
        )
        await asyncio.wait_for(model.started.wait(), 10)
        scope = {
            "project_id": "p",
            "thread_id": first["thread_id"],
            "contract_version": 2,
            "instruction": "Replacement question",
            "edit_kind": "REPLACE",
        }
        rejected = await runtime.bus.dispatch(
            request("thread/input", "bad-cas", {**scope, "expected_request_epoch": True})
        )
        assert rejected.error is not None
        second = value(
            await runtime.bus.dispatch(
                request("thread/input", "replace", {**scope, "expected_request_epoch": 1})
            )
        )
        model.release.set()
        await runtime.bus.drain()
        status = value(
            await runtime.bus.dispatch(
                request(
                    "thread/read",
                    "replacement",
                    {"project_id": "p", "thread_id": second["thread_id"]},
                )
            )
        )
        assert status["request"]["effective_question"] == "Replacement question"
        assert len(status["inputs"]) == 1
        history = value(
            await runtime.bus.dispatch(
                request(
                    "thread/activity/list",
                    "history",
                    {"project_id": "p", "thread_id": first["thread_id"]},
                )
            )
        )
        assert any(
            a["payload"].get("input_state") == "SUPERSEDED"
            for a in history["activities"]
            if a["event_type"] == "research/inputAccepted"
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_unsupported_transport_does_not_claim_input_was_analyzed(tmp_path: Path):
    runtime = await setup(tmp_path, UnavailableModel(), source=False)
    try:
        _, status = await finished(runtime, "A question awaiting a supported model")
        assert status["current_result"]["terminal_reason"] == "CONTROLLED_TRANSPORT_UNSUPPORTED"
        assert status["inputs"][0]["state"] == "ACCEPTED"
        assert status["inputs"][0]["first_result_ref"] is None
        assert status["budget"]["actual_tokens"] is None
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_model_deadline_returns_partial_without_waiting_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def short_deadline(_self: RequestBoundary) -> float:
        return 0.01

    monkeypatch.setattr(RequestBoundary, "call_timeout", short_deadline)
    runtime = await setup(tmp_path, ControlledResearchModel(wait=True), source=False)
    try:
        _, status = await finished(runtime, "A bounded investigation")
        assert status["current_result"]["terminal_reason"] == "MODEL_CALL_TIME_BUDGET_EXHAUSTED"
        assert status["inputs"][0]["state"] == "ACCEPTED"
    finally:
        runtime.close()
