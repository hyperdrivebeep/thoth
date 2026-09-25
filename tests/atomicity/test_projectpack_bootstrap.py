from pathlib import Path

import pytest
from tests.atomicity.harness import assert_phase_delta, snapshot

from thoth.adapters.projectpacks import load_project_pack
from thoth.adapters.storage.bundle import SqliteStoreFactory
from thoth.adapters.storage.governance import SqliteGovernanceStore
from thoth.apps.projectpack_execution import run_project_pack
from thoth.domain.governance import ProjectPolicy


async def test_projectpack_policy_failure_cannot_publish_orphan_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = load_project_pack(
        Path(__file__).resolve().parents[2] / "examples/projectpacks/demo-system",
        include_scripted=True,
    )
    stores = SqliteStoreFactory().open(tmp_path)
    before = snapshot(stores.ledger.engine)
    stores.close()
    original = SqliteGovernanceStore.put_policy

    def fail(store: SqliteGovernanceStore, policy: ProjectPolicy) -> None:
        original(store, policy)
        raise RuntimeError("bootstrap policy fault")

    with monkeypatch.context() as patch:
        patch.setattr(SqliteGovernanceStore, "put_policy", fail)
        with pytest.raises(RuntimeError, match="bootstrap policy fault"):
            await run_project_pack(pack, workspace=tmp_path)
    reopened = SqliteStoreFactory().open(tmp_path)
    try:
        assert_phase_delta(before, snapshot(reopened.ledger.engine))
    finally:
        reopened.close()
