from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import cast

import pytest

from thoth.application.services.field_execution_tools import (
    generate_counterbalanced_assignments,
    validate_sealed_baseline,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.field_measurement import SealedFieldBaseline


def test_six_by_six_assignment_is_balanced_and_collision_free() -> None:
    root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (root / "field-validation" / "sealed-baseline.json").read_text(encoding="utf-8")
    )
    actual = {case_id: digest for case_id, digest in payload["case_digests"].items()}
    baseline = validate_sealed_baseline(payload, actual_case_digests=actual)
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


def test_assignment_rejects_duplicate_identity_and_stale_baseline_digest() -> None:
    payload = {
        "baseline_id": "baseline:test",
        "version": "1",
        "case_digests": {f"case:{index}": str(index) * 64 for index in range(1, 7)},
        "sequence_matrix": ["ABC", "BCA", "CAB", "ACB", "CBA", "BAC"],
        "baseline_toolchain": ["manual", "gpt", "thoth"],
        "hard_zero_metrics": ["unauthorized_r3"],
        "timebox_seconds": 1800,
        "sealed_at": "2026-09-02T00:00:00Z",
        "baseline_digest": "0" * 64,
    }
    with pytest.raises(ValueError, match="digest"):
        validate_sealed_baseline(
            payload,
            actual_case_digests=cast(dict[str, str], payload["case_digests"]),
        )


@pytest.mark.parametrize("tamper", [False, True])
def test_assignment_generator_cli_validates_sealed_case_fixture(
    tmp_path: Path, tamper: bool
) -> None:
    root = Path(__file__).resolve().parents[2]
    # The historical field baseline must not be resealed when product pack policy changes.
    # Exercise the CLI with six independently sealed temporary cases instead.
    payload = json.loads((root / "field-validation" / "sealed-baseline.json").read_text())
    paths, digests = {}, {}
    for index in range(6):
        case = tmp_path / f"case-{index}"
        case.mkdir()
        content = f"bounded assignment fixture {index}".encode()
        (case / "case.txt").write_bytes(content)
        paths[f"case:{index}"] = case.as_posix()
        digests[f"case:{index}"] = hashlib.sha256(b"case.txt" + content).hexdigest()
    model = SealedFieldBaseline.model_validate(
        {
            **payload,
            "baseline_id": "baseline:cli-fixture",
            "case_paths": paths,
            "case_digests": digests,
            "baseline_digest": "0" * 64,
        }
    )
    sealed = model.model_copy(
        update={
            "baseline_digest": domain_digest(
                "FIELD_SEALED_BASELINE",
                "1.0.0",
                canonical_payload(model.model_dump(mode="python", exclude={"baseline_digest"})),
            )
        }
    )
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
