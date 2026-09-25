from pathlib import Path

import pytest
from sqlalchemy import text
from tests.integration.storage_coverage_helpers import prepare_project, request, value

from thoth.adapters.storage.governance_history import SqliteGovernanceHistory
from thoth.adapters.storage.projects import SqliteProjectStore


@pytest.mark.parametrize("damage", ["historical_receipt", "head", "projection"])
async def test_governance_readers_reject_detached_or_corrupt_basis(
    tmp_path: Path, damage: str
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/metadata/update",
                    "rename",
                    {"project_id": project, "expected_revision": 0, "name": "Updated"},
                )
            )
        )
        with runtime.ledger.engine.begin() as connection:
            if damage == "historical_receipt":
                connection.execute(
                    text(
                        "UPDATE governance_revisions SET receipt_digest=:bad "
                        "WHERE project_id=:project AND record_kind='PROJECT' AND revision=1"
                    ),
                    {"project": project, "bad": "0" * 64},
                )
            elif damage == "head":
                connection.execute(
                    text(
                        "UPDATE governance_heads SET record_digest=:bad "
                        "WHERE project_id=:project AND record_kind='PROJECT'"
                    ),
                    {"project": project, "bad": "0" * 64},
                )
            else:
                connection.execute(
                    text("UPDATE projects SET name='unpublished edit' WHERE project_id=:project"),
                    {"project": project},
                )
        with pytest.raises(ValueError, match="GOVERNANCE"):
            if damage == "historical_receipt":
                SqliteGovernanceHistory(runtime.ledger.engine).history(project, "PROJECT", project)
            else:
                SqliteProjectStore(runtime.ledger.engine).read(project)
    finally:
        runtime.close()
