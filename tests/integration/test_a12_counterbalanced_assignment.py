from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from tests.integration.synthetic_field_baseline import synthetic_sealed_baseline

from thoth.application.services.field_execution_tools import (
    generate_counterbalanced_assignments,
    validate_sealed_baseline,
)


def test_six_by_six_assignment_is_balanced_and_collision_free(tmp_path: Path) -> None:
    baseline = synthetic_sealed_baseline(tmp_path)
    assert baseline.external_results == "NOT_RUN"
    reviewers = tuple(f"reviewer:{ordinal:02d}" for ordinal in range(1, 7))
    manifest = generate_counterbalanced_assignments(
        baseline=baseline,
        reviewer_pseudonyms=reviewers,
    )
    assert len(manifest.assignments) == 36
    assert manifest.collision_free is True
    assert manifest.external_sessions == "NOT_RUN"
    reviewer_case = {(item.reviewer_pseudonym, item.case_id) for item in manifest.assignments}
    assert len(reviewer_case) == 36
    for reviewer in reviewers:
        counts = Counter(
            item.arm for item in manifest.assignments if item.reviewer_pseudonym == reviewer
        )
        assert counts == {"A": 2, "B": 2, "C": 2}
    for case_id in baseline.case_digests:
        counts = Counter(item.arm for item in manifest.assignments if item.case_id == case_id)
        assert counts == {"A": 2, "B": 2, "C": 2}


def test_assignment_rejects_duplicate_identity_and_stale_baseline_digest(tmp_path: Path) -> None:
    baseline = synthetic_sealed_baseline(tmp_path)
    payload = baseline.model_dump(mode="json")
    payload["baseline_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        validate_sealed_baseline(payload, actual_case_digests=baseline.case_digests)
    with pytest.raises(ValueError, match="unique"):
        generate_counterbalanced_assignments(
            baseline=baseline, reviewer_pseudonyms=("reviewer:01",) * 6
        )


@pytest.mark.parametrize("tamper", [False, True])
def test_assignment_generator_cli_validates_sealed_case_fixture(
    tmp_path: Path, tamper: bool
) -> None:
    root = Path(__file__).resolve().parents[2]
    sealed = synthetic_sealed_baseline(tmp_path)
    assert len(sealed.case_paths) == 6 and sealed.external_results == "NOT_RUN"
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(sealed.model_dump_json(), encoding="utf-8")
    if tamper:
        (tmp_path / "case-0" / "case.txt").write_text("changed after sealing")
    reviewers = tmp_path / "reviewers.json"
    output = tmp_path / "assignments.json"
    reviewers.write_text(
        json.dumps([f"reviewer:{ordinal:02d}" for ordinal in range(1, 7)]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "field-validation" / "assignment-generator.py"),
            "--baseline",
            str(baseline_path),
            "--reviewers",
            str(reviewers),
            "--output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if tamper:
        assert completed.returncode != 0
        assert "case digest mismatch" in completed.stderr
        assert not output.exists()
        return
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert len(manifest["assignments"]) == 36
    assert manifest["external_sessions"] == "NOT_RUN"
