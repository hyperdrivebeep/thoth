from __future__ import annotations

from pathlib import Path

from thoth.adapters.storage import SqliteCriterionContractStore
from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.criterion_contract import CriterionProfileRecord


def test_latest_enabled_profile_version_supersedes_without_mutating_v1(tmp_path: Path) -> None:
    runtime = create_runtime(tmp_path / "profile-upgrade")
    try:
        store = SqliteCriterionContractStore(runtime.ledger.engine)
        original = store.read_profile("AI_TEVV", 1)
        assert original is not None
        draft = original.model_dump(mode="python", exclude={"profile_digest"})
        draft.update(
            {
                "version": 2,
                "applicability_terms": (*original.applicability_terms, "red team"),
            }
        )
        upgraded = CriterionProfileRecord.model_validate(
            {
                **draft,
                "profile_digest": domain_digest(
                    "CRITERION_PROFILE",
                    "1.0.0",
                    canonical_payload(draft),
                ),
            }
        )
        store.put_profile(upgraded)
        assert store.read_profile("AI_TEVV", None) == upgraded
        assert store.read_profile("AI_TEVV", 1) == original
        latest = [
            item for item in store.list_profiles(enabled_only=True) if item.profile_ref == "AI_TEVV"
        ]
        assert [item.version for item in latest] == [2]
    finally:
        runtime.close()
