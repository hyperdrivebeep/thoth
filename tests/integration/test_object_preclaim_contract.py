"""Object authorization rejects an out-of-scope thread before input validation or claiming work."""

from pathlib import Path

from tests.atomicity.harness import snapshot
from tests.integration.resource_scope_helpers import denial, scope_harness, value


async def test_materialize_authorization_precedes_validation_and_operation_claim(
    tmp_path: Path,
) -> None:
    async with scope_harness(tmp_path) as h:
        value(
            await h.call(
                "alpha",
                "thread/start",
                "private-preclaim-thread",
                {
                    "thread_id": "thread:preclaim",
                    "problem": "Private problem",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        before = snapshot(h.runtime.ledger.engine)
        # The empty purpose would fail MaterializeInput validation if the scope gate moved later.
        denied = await h.call(
            "beta",
            "object/materialize",
            "denied-preclaim",
            {
                "thread_id": "thread:preclaim",
                "purpose_statement": "",
                "focus_refs": [],
                "trigger_evidence_refs": [],
                "actor_ref": h.actors["beta"],
            },
        )
        denial(denied, "AUTH_DATA_SCOPE_DENIED")
        assert denied.json()["error"]["data"]["pre_io"] is True
        after = snapshot(h.runtime.ledger.engine)
        for table in (
            "operations",
            "idempotency_keys",
            "events",
            "receipts",
            "semantic_revisions",
            "object_candidates",
            "decision_objects",
            "threads",
        ):
            assert after[table] == before[table], table
