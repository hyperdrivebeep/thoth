import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_restore_apply_atomicity import CHANGES, handler, input_for
from tests.integration.test_restore_preview_contract import prepared, revise, rpc_record

from thoth.apps.runtime import create_runtime
from thoth.apps.runtime_types import AppRuntime
from thoth.protocol.bus import DispatchTicket
from thoth.protocol.jsonrpc import JsonRpcResponse


async def test_two_engines_race_exactly_one_publication_and_reopen_replay(
    tmp_path: Path,
) -> None:
    first, _model, _accepted, candidates = await prepared(tmp_path)
    second = create_runtime(
        tmp_path,
        model_resolver=ControlledResearchModel(),
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        handler(first)
        handler(second)
        revision, snap = candidates["hypothesis.v1"]
        changed = revise(first, revision, snap, *CHANGES["hypothesis.v1"])
        payload = await input_for(first, revision, changed, "preview")
        before = len(first.ledger.read_revisions("p", "HYPOTHESIS", revision.entity_id))
        barrier = Barrier(2)

        def run(runtime: AppRuntime, key: str) -> tuple[str, JsonRpcResponse]:
            command = request("revision/restore/apply", key, payload)
            ticket = runtime.bus.claim(command)
            assert isinstance(ticket, DispatchTicket)
            barrier.wait(timeout=10)
            return key, asyncio.run(runtime.bus.execute(ticket))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, first, "writer-a"), pool.submit(run, second, "writer-b")]
            results = [future.result(timeout=30) for future in futures]
        successes = [(key, response) for key, response in results if response.error is None]
        failures = [response for _, response in results if response.error is not None]
        assert len(successes) == len(failures) == 1
        assert failures[0].error is not None
        assert failures[0].error.data["reason_code"] == "RESTORE_HEAD_CHANGED"
        assert len(first.ledger.read_revisions("p", "HYPOTHESIS", revision.entity_id)) == before + 1
        winner, response = successes[0]
        expected = rpc_record(response)
    finally:
        first.close()
        second.close()
    reopened = create_runtime(
        tmp_path,
        model_resolver=ControlledResearchModel(),
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        before_replay = snapshot(reopened.ledger.engine)
        assert (
            rpc_record(
                await reopened.bus.dispatch(request("revision/restore/apply", winner, payload))
            )
            == expected
        )
        assert_phase_delta(before_replay, snapshot(reopened.ledger.engine))
    finally:
        reopened.close()


async def test_running_claim_remains_unknown_across_second_caller_and_restart(
    tmp_path: Path,
) -> None:
    runtime, _model, _accepted, candidates = await prepared(tmp_path)
    try:
        host = handler(runtime)
        revision, snap = candidates["hypothesis.v1"]
        changed = revise(runtime, revision, snap, *CHANGES["hypothesis.v1"])
        payload = await input_for(runtime, revision, changed, "preview")
        command = request("revision/restore/apply", "unobserved-worker", payload)
        ticket = runtime.bus.claim(command)
        assert isinstance(ticket, DispatchTicket)
        before = snapshot(runtime.ledger.engine)
        replay = await runtime.bus.dispatch(command)
        assert replay.result is not None
        assert replay.result["state"] == "RUNNING"
        assert replay.result["execution_observation"] == "UNKNOWN"
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        operation = host.publication.operations.read(ticket.operation.operation_id)
        assert operation is not None and operation.state.value == "RUNNING"
    finally:
        runtime.close()
    reopened = create_runtime(
        tmp_path,
        model_resolver=ControlledResearchModel(),
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        before = snapshot(reopened.ledger.engine)
        replay = await reopened.bus.dispatch(command)
        assert replay.result is not None
        assert replay.result["state"] == "RUNNING"
        assert replay.result["execution_observation"] == "UNKNOWN"
        assert_phase_delta(before, snapshot(reopened.ledger.engine))
    finally:
        reopened.close()
