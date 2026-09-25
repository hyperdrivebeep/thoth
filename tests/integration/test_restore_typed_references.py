from collections.abc import Sequence
from pathlib import Path
from types import MethodType
from typing import cast

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_restore_apply_atomicity import CHANGES, handler, input_for
from tests.integration.test_restore_preview_contract import prepared, revise

from thoth.application.services.restore_references import (
    HypothesisRestoreReferences,
    RestoreReferenceRegistry,
)
from thoth.domain.hypothesis_full import HypothesisRecord
from thoth.domain.restore import RestoreSelection
from thoth.domain.test_validity import TestValidityAssessment as _TestValidityAssessment


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _refs(value: object) -> Sequence[str]:
    assert isinstance(value, (list, tuple))
    assert all(isinstance(item, str) for item in cast(Sequence[object], value))
    return cast(Sequence[str], value)


async def test_valid_assumption_owner_is_resolved_and_missing_reference_holds(tmp_path: Path):
    runtime, _model, _accepted, candidates = await prepared(tmp_path)
    try:
        host = handler(runtime)
        original, original_snapshot = candidates["hypothesis.v1"]
        added = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/assumption/add",
                    "assumption",
                    {
                        "project_id": "p",
                        "hypothesis_id": original.entity_id,
                        "expected_revision_digest": original.revision_digest,
                        "statement": "Fixture sampling is representative",
                        "role": "SAMPLING",
                        "evidence_refs": list(_refs(original_snapshot.content["evidence_refs"])),
                    },
                )
            )
        )
        assert added
        target_digest = runtime.ledger.read_heads("p")[f"HYPOTHESIS:{original.entity_id}"]
        target = runtime.ledger.read_revision_by_digest("p", target_digest)
        assert target is not None
        target_snapshot = runtime.ledger.read_snapshot(target.snapshot_id)
        assert target_snapshot is not None
        assert _refs(target_snapshot.content["assumption_refs"])
        changed = revise(runtime, target, target_snapshot, *CHANGES["hypothesis.v1"])
        payload = await input_for(runtime, target, changed, "with-assumption")
        plan = host.planner.build(RestoreSelection.model_validate(payload["selection"]))
        records = _record(_record(plan.basis.consumer_basis["typed_references"])["records"])
        assert set(records) == set(_refs(target_snapshot.content["assumption_refs"]))
        applied = value(
            await runtime.bus.dispatch(
                request("revision/restore/apply", "apply-with-assumption", payload)
            )
        )
        assert applied["status"] == "APPLIED"
        current = runtime.ledger.read_revision_by_digest("p", applied["new_revision_digest"])
        assert current is not None
        current_snapshot = runtime.ledger.read_snapshot(current.snapshot_id)
        assert current_snapshot is not None
        malformed = revise(
            runtime, current, current_snapshot, "assumption_refs", ["assumption:foreign-or-missing"]
        )
        preview = value(
            await runtime.bus.query(
                request(
                    "revision/restore/preview",
                    "missing",
                    {
                        "project_id": "p",
                        "selection": {
                            "project_id": "p",
                            "entity_type": "HYPOTHESIS",
                            "entity_id": original.entity_id,
                            "expected_current_head": malformed.revision_digest,
                            "target_revision_digest": malformed.revision_digest,
                        },
                        "contract_version": 2,
                    },
                )
            )
        )
        assert preview["availability"] == "BLOCKED" and preview["reason_codes"] == [
            "RESTORE_IMPACT_INCOMPLETE"
        ]
    finally:
        runtime.close()


async def test_sealed_prediction_and_test_refs_restore_without_reexecuting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_hypothesis_prediction_outcome_cycle import prepare_measurement

    runtime, sandbox, project, thread, _contract = await prepare_measurement(tmp_path, monkeypatch)
    try:
        host = handler(runtime)
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/input", "measure-once", {"project_id": project, "thread_id": thread}
                )
            )
        )
        from thoth.adapters.storage.hypothesis import SqliteHypothesisStore

        records = SqliteHypothesisStore(runtime.ledger.engine, runtime.ledger).list_hypotheses(
            project
        )
        record = next(record for record in records if record.test_refs and record.prediction_refs)
        calls = len(sandbox.seen_specs)
        changed = value(
            await runtime.bus.dispatch(
                request(
                    "hypothesis/revise",
                    "change-after-test",
                    {
                        "project_id": project,
                        "hypothesis_id": record.hypothesis_id,
                        "expected_revision_digest": record.revision_digest,
                        "patch": {"statement": "Changed after measurement"},
                        "reason": "Exercise historical tested hypothesis",
                        "evidence_refs": list(record.evidence_refs),
                    },
                )
            )
        )["hypothesis"]
        selection = {
            "project_id": project,
            "entity_type": "HYPOTHESIS",
            "entity_id": record.hypothesis_id,
            "target_revision_digest": record.revision_digest,
            "expected_current_head": changed["revision_digest"],
        }
        preview = value(
            await runtime.bus.query(
                request(
                    "revision/restore/preview",
                    "sealed-preview",
                    {"project_id": project, "selection": selection, "contract_version": 2},
                )
            )
        )
        assert preview["availability"] == "AVAILABLE", preview["reason_codes"]
        assert len(sandbox.seen_specs) == calls
        plan = host.planner.build(RestoreSelection.model_validate(selection))
        proof = _record(_record(plan.basis.consumer_basis["typed_references"])["records"])
        assert set(record.test_refs).issubset(proof) and set(record.prediction_refs).issubset(proof)
        registry = host.planner.references
        assert isinstance(registry, RestoreReferenceRegistry)
        reader = registry.readers[HypothesisRecord]
        assert isinstance(reader, MethodType)
        resolver = reader.__self__
        assert isinstance(resolver, HypothesisRestoreReferences)
        original_read = resolver.tests.read_assessment
        with monkeypatch.context() as patch:

            def foreign(project_id: str, identifier: str) -> _TestValidityAssessment | None:
                stored = original_read(project_id, identifier)
                return (
                    None
                    if stored is None
                    else stored.model_copy(update={"object_id": "foreign-object"})
                )

            patch.setattr(resolver.tests, "read_assessment", foreign)
            foreign_preview = value(
                await runtime.bus.query(
                    request(
                        "revision/restore/preview",
                        "foreign-proof",
                        {"project_id": project, "selection": selection, "contract_version": 2},
                    )
                )
            )
            assert foreign_preview["reason_codes"] == ["RESTORE_TARGET_MISMATCH"]
        applied = value(
            await runtime.bus.dispatch(
                request(
                    "revision/restore/apply",
                    "restore-tested",
                    {
                        "project_id": project,
                        "selection": selection,
                        "preview_basis_digest": preview["basis_digest"],
                        "reason": "Restore historical content, never repeat the measurement",
                    },
                )
            )
        )
        assert applied["status"] == "APPLIED" and len(sandbox.seen_specs) == calls
    finally:
        runtime.close()
