from datetime import UTC, datetime

from thoth.application.commands.sources import SourceTimeConfirmInput, SourceTimeCorrectInput
from thoth.domain.enums import CutoffState
from thoth.domain.source_time import SourceTimeAssertion


def test_confirm_requires_mutation_basis_and_unknown_assertion() -> None:
    value = SourceTimeConfirmInput.model_validate(
        {
            "project_id": "project:t",
            "artifact_id": "artifact:t",
            "source_version_id": "source-version:t",
            "byte_sha256": "a" * 64,
            "expected_project_revision": 1,
            "expected_cutoff_at": datetime(2026, 1, 1, tzinfo=UTC),
            "expected_assessment_revision": 0,
            "expected_metadata_digest": "b" * 64,
            "assertion": SourceTimeAssertion.ON_OR_BEFORE_CUTOFF,
        }
    )
    assert value.assertion is SourceTimeAssertion.ON_OR_BEFORE_CUTOFF
    dumped = SourceTimeConfirmInput.model_json_schema()
    assert "actor_id" not in dumped.get("properties", {})
    assert set(CutoffState) == {
        CutoffState.ELIGIBLE,
        CutoffState.AFTER_CUTOFF,
        CutoffState.UNKNOWN_TIME,
        CutoffState.PROHIBITED_CONTEXT,
    }


def test_correct_input_keeps_relative_assertion() -> None:
    value = SourceTimeCorrectInput.model_validate(
        {
            "project_id": "project:t",
            "artifact_id": "artifact:t",
            "source_version_id": "source-version:t",
            "byte_sha256": "a" * 64,
            "expected_project_revision": 1,
            "expected_cutoff_at": datetime(2026, 1, 1, tzinfo=UTC),
            "expected_assessment_revision": 0,
            "expected_metadata_digest": "b" * 64,
            "assertion": SourceTimeAssertion.AFTER_CUTOFF,
            "correction_reason": "observed issued date was misread",
        }
    )
    assert value.revert_unknown is False
