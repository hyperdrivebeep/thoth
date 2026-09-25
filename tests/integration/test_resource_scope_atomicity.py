"""HTTP scope mutation faults roll back the whole policy transition; stale peers lose CAS."""

import asyncio
from pathlib import Path
from threading import Barrier
from typing import Any

import httpx
import pytest
from sqlalchemy import event
from tests.integration.resource_scope_helpers import scope_harness, scope_peer, value
from tests.integration.storage_coverage_helpers import domain_snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point",
    [
        "UPDATE RESOURCE_SCOPE_HEADS",
        "INSERT INTO RESOURCE_SCOPE_HISTORY",
        "INSERT INTO RESOURCE_SCOPE_RECEIPTS",
    ],
)
async def test_scope_grant_rolls_back_after_each_durable_write(tmp_path: Path, point: str) -> None:
    async with scope_harness(tmp_path) as h:
        resource = value(
            await h.connect(
                "alpha",
                "fault-source",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )["artifact"]["artifact_id"]
        scope = value(
            await h.call("alpha", "project/source/scope/read", "before", {"resource_ref": resource})
        )["scope"]
        before = domain_snapshot(h.runtime.ledger.engine)
        hits: list[str] = []

        def fail_after_write(
            _connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _many: bool,
        ) -> None:
            if statement.lstrip().upper().startswith(point) and not hits:
                hits.append(point)
                raise RuntimeError("injected scope transition write fault")

        event.listen(h.runtime.ledger.engine, "after_cursor_execute", fail_after_write)
        try:
            response = await h.call(
                "alpha",
                "project/source/scope/grant",
                "grant-fault",
                {
                    "resource_ref": resource,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "fault scenario",
                },
            )
            assert "error" in response.json()
            assert hits == [point]
        finally:
            event.remove(h.runtime.ledger.engine, "after_cursor_execute", fail_after_write)
        assert domain_snapshot(h.runtime.ledger.engine) == before
        async with scope_peer(h) as peer:
            current = value(
                await peer.call(
                    "alpha", "project/source/scope/read", "reopened", {"resource_ref": resource}
                )
            )["scope"]
            assert current == scope
            assert domain_snapshot(peer.runtime.ledger.engine) == before


@pytest.mark.asyncio
async def test_two_runtime_scope_grants_have_one_cas_winner(tmp_path: Path) -> None:
    async with scope_harness(tmp_path) as h:
        resource = value(
            await h.connect(
                "alpha",
                "race-source",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )["artifact"]["artifact_id"]
        scope = value(
            await h.call(
                "alpha", "project/source/scope/read", "race-basis", {"resource_ref": resource}
            )
        )["scope"]
        barrier = Barrier(2)

        async with scope_peer(h) as first, scope_peer(h) as second:
            peers = (first, second)

            async def run_peer(ordinal: int) -> httpx.Response:
                peer = peers[ordinal]
                barrier.wait(timeout=20)
                return await peer.call(
                    "alpha",
                    "project/source/scope/grant",
                    f"race-{ordinal}",
                    {
                        "resource_ref": resource,
                        "expected_revision": scope["revision"],
                        "grantee_kind": "ACTOR" if ordinal == 0 else "WORKSTREAM",
                        "grantee_ref": h.actors["beta"] if ordinal == 0 else "beta",
                        "reason": "same-version concurrent policy edit",
                    },
                )

            def run_thread(ordinal: int) -> httpx.Response:
                return asyncio.run(run_peer(ordinal))

            results = await asyncio.gather(*(asyncio.to_thread(run_thread, i) for i in range(2)))
        bodies = [response.json() for response in results]
        assert sum("result" in body for body in bodies) == 1
        errors = [body["error"] for body in bodies if "error" in body]
        assert errors[0]["data"]["reason_code"] == "RESOURCE_SCOPE_REVISION_CONFLICT"
        async with scope_peer(h) as peer:
            final = value(
                await peer.call(
                    "alpha", "project/source/scope/read", "final", {"resource_ref": resource}
                )
            )["scope"]
            assert final["revision"] == scope["revision"] + 1
            assert len(final["grants"]) == 1
