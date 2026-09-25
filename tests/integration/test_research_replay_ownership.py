"""Independent controlled audit of repaired runtime; all state lives in tmp_path."""

import asyncio
import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a13_operation_scope import auth_service
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.apps.runtime import AppRuntime
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope
from thoth.domain.resource_scope import resource_use_scope
from thoth.protocol.bus import DispatchTicket
from thoth.protocol.deferred import AcceptedRunning, current_operation


def operation_snapshot(runtime: AppRuntime, operation_id: str) -> str:
    operation = runtime.bus.read_operation(operation_id)
    assert operation is not None
    return json.dumps(
        {
            "operation": operation.model_dump(mode="json"),
            "heads": dict(runtime.ledger.read_heads("p")),
            "journals": [
                r.model_dump(mode="json")
                for r in SqliteControlRecordStore(runtime.ledger.engine).list(
                    "p", "RESEARCH_EXECUTION", latest_only=False
                )
            ],
        },
        sort_keys=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("caller", ["peer", "alpha_new", "anonymous"])
async def test_peer_replay_must_not_damage_originating_operation(
    tmp_path: Path, admitted: bool, caller: str
) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    auth = auth_service(runtime)
    actors: dict[str, AuthenticatedActorContext] = {}
    try:
        for revision, name in enumerate(("alpha", "peer")):
            assigned = value(
                await runtime.bus.dispatch(
                    request(
                        "project/role/assign",
                        name,
                        {
                            "project_id": "p",
                            "expected_revision": revision,
                            "actor_id": "human:" + name,
                            "role": "researcher",
                            "scope": "PROJECT",
                            "authority_tags": ["CAP_READ", "CAP_WRITE", "CAP_THREAD"],
                        },
                    )
                )
            )
            issued = await auth.issue_http_session(
                actor_id="human:" + name,
                credential="test-" + name,
                project_id="p",
                role_assignment_id=assigned["role"]["role_assignment_id"],
            )
            actors[name] = await auth.authenticate_http(
                authorization="Bearer " + issued.bearer_token,
                project_id="p",
                method="thread/start",
                requested_scope={},
            )
        req = request(
            "thread/start",
            "original-authenticated-key",
            {
                "project_id": "p",
                "problem": "Original authenticated submission",
                "contract_version": 2,
            },
        )
        # T1 committed before scheduling, retaining real authentication records.
        with authenticated_actor_scope(actors["alpha"]):
            ticket = runtime.bus.claim(req)
            assert isinstance(ticket, DispatchTicket)
            original_id = ticket.operation.operation_id
            if admitted:
                token = current_operation.set(ticket.operation)
                try:
                    with resource_use_scope("p"):
                        accepted = await ticket.handler(req.params.input)
                        assert isinstance(accepted, AcceptedRunning)
                finally:
                    current_operation.reset(token)
        issued = await auth.issue_http_session(
            actor_id="human:alpha",
            credential="test-alpha",
            project_id="p",
            role_assignment_id=actors["alpha"].role_assignment_id,
        )
        actors["alpha_new"] = await auth.authenticate_http(
            authorization="Bearer " + issued.bearer_token,
            project_id="p",
            method="thread/start",
            requested_scope={},
        )
        before = operation_snapshot(runtime, original_id)
        with nullcontext() if caller == "anonymous" else authenticated_actor_scope(actors[caller]):
            direct = await runtime.bus.execute(ticket)
            assert direct.error is not None
            replay = await runtime.bus.dispatch(req)
            await asyncio.wait_for(runtime.bus.drain(), 15)
        operation = runtime.bus.read_operation(original_id)
        assert replay.error is not None
        assert operation_snapshot(runtime, original_id) == before
        assert not model.calls
        with authenticated_actor_scope(actors["alpha"]):
            owner_reply = await runtime.bus.dispatch(req)
            assert owner_reply.error is None
            await runtime.bus.drain()
        completed = runtime.bus.read_operation(original_id)
        assert completed is not None and completed.state.value == "SUCCEEDED"
        # Desired acceptance, not a bug-observation assertion.
        assert operation is not None and operation.state.value == "RUNNING", (
            "Peer replay must not fail owner's accepted work"
        )
    finally:
        runtime.close()
