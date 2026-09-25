"""A valid digest alone does not excuse missing scope receipts or mismatched owner rows."""

from pathlib import Path

import pytest
from sqlalchemy import delete, update
from tests.integration.resource_scope_helpers import scope_harness, value

from thoth.adapters.storage.resource_scope import SqliteResourceScopeStore
from thoth.adapters.storage.resource_scope_schema import (
    resource_scope_history,
    resource_scope_receipts,
)
from thoth.domain.resource_scope import ResourceScopeError


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["missing_receipt", "history_digest"])
async def test_scope_read_refuses_incomplete_or_inconsistent_seal(
    tmp_path: Path, corruption: str
) -> None:
    async with scope_harness(tmp_path) as h:
        resource = value(
            await h.connect(
                "alpha",
                "integrity-source",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )["artifact"]["artifact_id"]
        with h.runtime.ledger.engine.begin() as connection:
            if corruption == "missing_receipt":
                connection.execute(
                    delete(resource_scope_receipts).where(
                        resource_scope_receipts.c.resource_ref == resource
                    )
                )
            else:
                connection.execute(
                    update(resource_scope_history)
                    .where(resource_scope_history.c.resource_ref == resource)
                    .values(record_digest="0" * 64)
                )
        with pytest.raises(ResourceScopeError):
            SqliteResourceScopeStore(h.runtime.ledger.engine).read(h.project, resource)
