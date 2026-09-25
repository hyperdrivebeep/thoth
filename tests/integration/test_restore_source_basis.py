from collections.abc import Sequence
from pathlib import Path
from typing import cast

from sqlalchemy import update
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_restore_apply_atomicity import handler
from tests.integration.test_restore_preview_contract import prepared

from thoth.adapters.storage.schema import evidence_spans
from thoth.domain.enums import CutoffState


def _first_ref(value: object) -> str:
    assert isinstance(value, (list, tuple)) and value
    assert all(isinstance(ref, str) for ref in cast(Sequence[object], value))
    first = cast(Sequence[object], value)[0]
    assert isinstance(first, str)
    return first


async def test_immutable_span_proof_is_project_bound_and_never_restores_current_cutoff(
    tmp_path: Path,
):
    runtime, model, _accepted, candidates = await prepared(tmp_path)
    try:
        host = handler(runtime)
        revision, snap = candidates["hypothesis.v1"]
        ref = _first_ref(snap.content["evidence_refs"])
        reader = host.planner.history
        proof = reader.read_immutable_span_basis("p", ref)
        assert proof is not None and proof.span_id == ref
        assert reader.read_immutable_span_basis("another-project", ref) is None
        span = host.planner.artifacts.read_evidence(ref)
        assert span is not None
        host.planner.artifacts.update_evidence(
            span.model_copy(update={"cutoff_state": CutoffState.AFTER_CUTOFF})
        )
        assert reader.read_immutable_span_basis("p", ref) == proof
        before = snapshot(runtime.ledger.engine)
        calls = len(model.calls)
        preview = value(
            await runtime.bus.query(
                request(
                    "revision/restore/preview",
                    "cutoff",
                    {
                        "project_id": "p",
                        "selection": {
                            "project_id": "p",
                            "entity_type": "HYPOTHESIS",
                            "entity_id": revision.entity_id,
                            "target_revision_digest": revision.revision_digest,
                            "expected_current_head": revision.revision_digest,
                        },
                        "contract_version": 2,
                    },
                )
            )
        )
        assert (
            preview["availability"] == "BLOCKED"
            and "RESTORE_SOURCE_DRIFT" in preview["reason_codes"]
        )
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        assert len(model.calls) == calls
        # Deliberately corrupt the isolated fixture's immutable hash to exercise decoder rejection.
        with runtime.ledger.engine.begin() as connection:
            connection.execute(
                update(evidence_spans)
                .where(evidence_spans.c.span_id == ref)
                .values(text_sha256="0" * 64)
            )
        assert reader.read_immutable_span_basis("p", ref) is None
    finally:
        runtime.close()
