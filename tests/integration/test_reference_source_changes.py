from pathlib import Path

import pytest
from tests.integration.reference_helpers import reference_harness
from tests.integration.storage_coverage_helpers import domain_snapshot, value

from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.domain.enums import AuthorityState, CutoffState, SupportState


@pytest.mark.parametrize("change", ["refuted", "future", "basis"])
async def test_pending_reference_answer_rechecks_actual_source_state(
    tmp_path: Path, change: str
) -> None:
    async with reference_harness(tmp_path) as h:
        first = value(
            await h.input(
                "ask",
                reference_request={
                    **h.request,
                    "target": {**h.request["target"], "denominator": None},
                },
            )
        )
        current = await h.read()
        inquiry = first["reference_inquiry"]
        question = inquiry["questions"][0]
        artifacts = SqliteArtifactLedger(h.runtime.ledger.engine)
        span = artifacts.read_evidence(h.request["measurements"][0]["span_id"])
        assert span is not None
        if change == "refuted":
            artifacts.update_evidence(
                span.model_copy(update={"support_state": SupportState.REFUTED})
            )
            reason = "REFERENCE_SOURCE_SUPPORT_UNRESOLVED"
        elif change == "future":
            artifact = artifacts.read_artifact(span.artifact_id)
            assert artifact is not None
            artifacts.update_artifact_cutoff(artifact, CutoffState.AFTER_CUTOFF)
            reason = "REFERENCE_SOURCE_CUTOFF_INELIGIBLE"
        else:
            artifacts.update_evidence(
                span.model_copy(update={"authority_state": AuthorityState.INFORMAL})
            )
            reason = "REFERENCE_SOURCE_BASIS_CHANGED"
        before = domain_snapshot(h.runtime.ledger.engine)
        result = await h.input(
            "answer-changed-source",
            reference_answer={
                "criterion_id": h.criterion["criterion_id"],
                "expected_revision_digest": current["revision_digest"],
                "inquiry_id": inquiry["inquiry_id"],
                "question_id": question["question_id"],
                "value": "per-request",
            },
        )
        assert result.error is not None
        assert reason in str(result.error)
        after = await h.read("unchanged-criterion")
        assert after["revision_digest"] == current["revision_digest"]
        # Source metadata changed independently; reference canonical revisions do not advance.
        assert (
            domain_snapshot(h.runtime.ledger.engine)["criterion_contracts"]
            == before["criterion_contracts"]
        )
