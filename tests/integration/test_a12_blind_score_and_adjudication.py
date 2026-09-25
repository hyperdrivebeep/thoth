from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from thoth.application.services.field_execution_tools import (
    adjudicate_blind_scores,
    build_blind_score_package,
    field_score_digest,
)
from thoth.domain.field_measurement import FieldScoreRecord


def score_payload(*, critical: int, completeness: int) -> dict[str, object]:
    payload: dict[str, object] = {
        "critical_issue_detected": critical,
        "decision_completeness_bps": completeness,
        "hard_zero_values": {"unauthorized_r3": 0},
    }
    payload["score_digest"] = field_score_digest(payload)
    return payload


def test_blind_package_removes_arm_branding_and_disagreement_requires_adjudication() -> None:
    package = build_blind_score_package(
        session_id="field-session:1",
        normalized_assessment={
            "decision": "HOLD pending comparable evidence",
            "evidence_refs": ["span:1"],
        },
        source_digest="a" * 64,
    )
    serialized = package.model_dump_json().casefold()
    assert '"arm":' not in serialized
    assert "thoth" not in serialized
    assert "reviewer" not in serialized
    assert package.identity_fields_removed is True

    first = score_payload(critical=5, completeness=9000)
    second = score_payload(critical=4, completeness=8200)
    required = adjudicate_blind_scores(first=first, second=second)
    assert required.state == "ADJUDICATION_REQUIRED"
    assert required.resolution is None
    resolved = adjudicate_blind_scores(
        first=first,
        second=second,
        adjudicator_pseudonym="adjudicator:01",
        resolution={"critical_issue_detected": 5, "decision_completeness_bps": 8800},
    )
    assert resolved.state == "RESOLVED"
    assert resolved.scorer_identity_exposed is False


def test_score_validator_cli_writes_typed_adjudication_ledger(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    output = tmp_path / "adjudication.json"
    first.write_text(
        json.dumps(
            {
                **score_payload(critical=5, completeness=9000),
            }
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                **score_payload(critical=4, completeness=8200),
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "field-validation" / "score-validator.py"),
            "--first",
            str(first),
            "--second",
            str(second),
            "--output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["state"] == "ADJUDICATION_REQUIRED"
    assert record["scorer_identity_exposed"] is False


def test_blind_score_rejects_tampered_values_with_stale_digest() -> None:
    stale = score_payload(critical=5, completeness=9000)
    stale["critical_issue_detected"] = 999
    peer = score_payload(critical=4, completeness=8200)

    with pytest.raises(ValueError, match="digest"):
        adjudicate_blind_scores(first=stale, second=peer)


def test_field_score_digest_survives_typed_json_roundtrip_and_rejects_duplicate_score() -> None:
    draft: dict[str, object] = {
        "score_id": "field-score:1",
        "project_id": "project:a12",
        "session_id": "field-session:1",
        "scorer_pseudonym": "scorer:independent-a",
        "gold_issue_total": 5,
        "critical_issue_detected": 4,
        "decision_completeness_bps": 8200,
        "source_span_valid_count": 4,
        "source_span_invalid_count": 0,
        "hard_zero_values": {"unauthorized_r3": 0},
        "recorded_at": datetime(2026, 9, 4, 1, 2, 3, 123456, tzinfo=UTC),
    }
    record = FieldScoreRecord.model_validate({**draft, "score_digest": field_score_digest(draft)})
    serialized = record.model_dump(mode="json")
    assert field_score_digest(serialized) == record.score_digest
    with pytest.raises(ValueError, match="independent"):
        adjudicate_blind_scores(first=serialized, second=serialized)
