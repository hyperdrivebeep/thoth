"""Legacy ownership is an explicit admin decision; transfers preserve source bytes."""

from pathlib import Path

import pytest
from sqlalchemy import delete
from tests.integration.resource_scope_helpers import denial, scope_harness, value

from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.resource_scope_schema import (
    resource_scope_heads,
    resource_scope_history,
    resource_scope_receipts,
)


@pytest.mark.asyncio
async def test_legacy_scope_assignment_and_owner_transfer_preserve_content(tmp_path: Path) -> None:
    async with scope_harness(tmp_path) as h:
        connected = value(
            await h.connect(
                "alpha",
                "legacy-content",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )
        resource = connected["artifact"]["artifact_id"]
        raw = SqliteArtifactLedger(h.runtime.ledger.engine)
        before = raw.read_artifact(resource)
        spans = raw.list_evidence(h.project)
        span = next(item for item in spans if "alpha-private-marker" in item.exact_text)
        # Fixture construction only: model a pre-scope database, without changing source records.
        with h.runtime.ledger.engine.begin() as connection:
            for table in (resource_scope_receipts, resource_scope_heads, resource_scope_history):
                connection.execute(
                    delete(table).where(
                        table.c.project_id == h.project, table.c.resource_ref == resource
                    )
                )
        denial(
            await h.call("alpha", "evidence/read", "legacy-unknown", {"span_id": span.span_id}),
            "RESOURCE_SCOPE_UNKNOWN",
        )
        assignment: dict[str, object] = {
            "resource_ref": resource,
            "expected_revision": 0,
            "scope": {
                "owner_kind": "WORKSTREAM",
                "owner_workstream": "alpha",
                "visibility": "WORKSTREAM",
            },
            "reason": "owner reviewed this legacy source and assigned alpha",
        }
        denial(
            await h.call("beta", "project/source/scope/assign", "unowned-claim", assignment),
            "RESOURCE_SCOPE_MANAGEMENT_DENIED",
        )
        assigned = value(
            await h.call("owner", "project/source/scope/assign", "legacy-reviewed", assignment)
        )["scope"]
        assert assigned["revision"] == 1
        assert "alpha-private-marker" in str(
            value(
                await h.call("alpha", "evidence/read", "assigned-read", {"span_id": span.span_id})
            )
        )
        transfer: dict[str, object] = {
            "resource_ref": resource,
            "expected_revision": assigned["revision"],
            "scope": {
                "owner_kind": "WORKSTREAM",
                "owner_workstream": "beta",
                "visibility": "WORKSTREAM",
            },
            "reason": "project owner transferred this source to beta",
        }
        denial(
            await h.call("alpha", "project/source/scope/update", "unapproved-transfer", transfer),
            "RESOURCE_OWNER_TRANSFER_DENIED",
        )
        moved = value(
            await h.call("owner", "project/source/scope/update", "approved-transfer", transfer)
        )["scope"]
        assert moved["owner_workstream"] == "beta"
        denial(
            await h.call(
                "alpha", "evidence/read", "former-owner-denied", {"span_id": span.span_id}
            ),
            "RESOURCE_ACCESS_DENIED",
        )
        assert "alpha-private-marker" in str(
            value(
                await h.call("beta", "evidence/read", "new-owner-read", {"span_id": span.span_id})
            )
        )
        assert raw.read_artifact(resource) == before
        assert raw.list_evidence(h.project) == spans
