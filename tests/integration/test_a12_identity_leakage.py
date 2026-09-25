from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from thoth.application.services.field_execution_tools import anonymize_field_export


def test_anonymizer_preserves_not_run_and_rejects_identity_or_raw_prompt_leakage() -> None:
    safe = anonymize_field_export(
        {
            "protocol_digest": "a" * 64,
            "sessions": [
                {
                    "session_id": "session:1",
                    "reviewer_pseudonym": "reviewer:abcd",
                    "arm": "C",
                    "active_milliseconds": 1200,
                }
            ],
            "scores": [{"score_digest": "b" * 64, "scorer_pseudonym": "scorer:efgh"}],
        }
    )
    text = safe.model_dump_json().casefold()
    assert "reviewer_pseudonym" not in text
    assert "scorer_pseudonym" not in text
    assert safe.external_participant_sessions == "NOT_RUN"
    assert safe.external_expert_validation == "NOT_RUN"
    assert safe.time_saving_result == "NOT_RUN"
    assert safe.wtp_result == "NOT_RUN"
    assert safe.d6_claimed is False

    with pytest.raises(ValueError, match=r"identity|privacy"):
        anonymize_field_export(
            {
                "protocol_digest": "a" * 64,
                "sessions": [{"reviewer_email": "person@example.com"}],
            }
        )
    with pytest.raises(ValueError, match=r"identity|privacy"):
        anonymize_field_export(
            {
                "protocol_digest": "a" * 64,
                "events": [{"note": "contact person@example.com for the raw result"}],
            }
        )
    with pytest.raises(ValueError, match=r"privacy|metadata"):
        anonymize_field_export(
            {
                "protocol_digest": "a" * 64,
                "events": [{"metadata": {"phone_number": 1012345678}}],
            }
        )


def test_export_anonymizer_cli_preserves_forced_not_run_fields(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "field-export.json"
    output = tmp_path / "anonymous.json"
    source.write_text(
        json.dumps(
            {
                "protocol_digest": "a" * 64,
                "sessions": [
                    {
                        "session_id": "session:1",
                        "reviewer_pseudonym": "reviewer:abcd",
                        "arm": "C",
                        "active_milliseconds": 1200,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "field-validation" / "export-anonymizer.py"),
            "--input",
            str(source),
            "--output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    anonymous = json.loads(output.read_text(encoding="utf-8"))
    assert anonymous["external_participant_sessions"] == "NOT_RUN"
    assert anonymous["time_saving_result"] == "NOT_RUN"
    assert anonymous["wtp_result"] == "NOT_RUN"
    assert anonymous["d6_claimed"] is False
    assert "reviewer_pseudonym" not in output.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=r"identity|privacy"):
        anonymize_field_export(
            {
                "protocol_digest": "a" * 64,
                "events": [{"raw_prompt": "private content"}],
            }
        )
