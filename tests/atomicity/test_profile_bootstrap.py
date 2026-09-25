from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.harness import Snapshot, assert_phase_delta, snapshot
from tests.atomicity.test_store_lifecycle import RecordingFactory

from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.outcome import SqliteOutcomeStore
from thoth.application.services.criterion_contract_service import CriterionContractService
from thoth.application.services.decision_object_service import DecisionObjectService
from thoth.application.services.outcome_service import OutcomeService
from thoth.apps.runtime import create_runtime


@pytest.mark.parametrize(
    "service_type,store_type",
    [
        (CriterionContractService, SqliteCriterionContractStore),
        (DecisionObjectService, SqliteDecisionObjectStore),
        (OutcomeService, SqliteOutcomeStore),
    ],
)
def test_profile_catalog_batch_cannot_publish_a_partial_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, service_type: Any, store_type: Any
) -> None:
    factory = RecordingFactory()
    before: list[Snapshot] = []
    original_seed = service_type.seed_profiles
    original_put = store_type.put_profile
    calls = 0

    def seed(service: Any) -> None:
        before.append(snapshot(factory.bundles[0].ledger.engine))
        original_seed(service)

    def put(store: Any, record: Any) -> None:
        nonlocal calls
        original_put(store, record)
        calls += 1
        if calls == 2:
            raise RuntimeError("second catalog profile fault")

    monkeypatch.setattr(service_type, "seed_profiles", seed)
    monkeypatch.setattr(store_type, "put_profile", put)
    with pytest.raises(RuntimeError, match="second catalog profile fault"):
        create_runtime(tmp_path, storage_factory=factory)
    assert calls == 2 and len(before) == 1
    assert_phase_delta(before[0], snapshot(factory.bundles[0].ledger.engine))
