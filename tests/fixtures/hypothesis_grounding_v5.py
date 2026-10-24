"""Synthetic canonical values, kept separate from the sealed G1 assessment fixtures."""

import json
from pathlib import Path
from typing import Any

from thoth.domain.response_language import LANGUAGE_POLICY_VERSION

LEGACY = Path(__file__).with_name("hypothesis_grounding_legacy.json")


def legacy_record(version: str = "1.2.0") -> dict[str, Any]:
    entries = json.loads(LEGACY.read_text(encoding="utf-8"))["records"]
    return json.loads(
        next(item["canonical_json"] for item in entries if item["version"] == version)
    )


def stage_input(stage: str = "GENERATOR", language: str | None = None) -> dict[str, Any]:
    return {
        "generating_request_ref": legacy_record()["generation_details"]["generating_request_ref"],
        "prompt_version": "hypothesis_portfolio.v5"
        if stage == "GENERATOR"
        else "hypothesis_table_filler.v6",
        "output_schema_digest": "a" * 64,
        "effective_context_digest": "b" * 64,
        "provider_input_digest": "c" * 64,
        "response_language": language,
        "language_policy_version": None if language is None else LANGUAGE_POLICY_VERSION,
        "cutoff_at": "2026-10-24T00:00:00Z",
        "model_policy_ref": "policy:fixture",
        "model_settings_digest": None,
        "max_output_tokens": 4000 if stage == "GENERATOR" else 3000,
    }


def prediction(role: str = "HYPOTHESIS_ONLY") -> dict[str, Any]:
    supported = role in ("OBSERVATION_RESTATEMENT", "EVIDENCE_INFERENCE")
    return {
        "hypothesis_id": "h1",
        "knowledge_state": "PREDICTED",
        "expected": "expected outcome",
        "basis": ["span:0"] if supported else [],
        "grounding": {"derivation_role": role, "rationale": "Premise only." if supported else ""},
    }


def stored_prediction(role: str = "HYPOTHESIS_ONLY") -> dict[str, Any]:
    row = prediction(role)
    return {
        **row,
        "source_stage": "GENERATOR",
        "source_bindings": [
            {"span_id": span, "source_version_id": "source:1", "text_sha256": "d" * 64}
            for span in row["basis"]
        ],
        "relation_assessment": {},
    }


def v5_record() -> dict[str, Any]:
    value = legacy_record()
    value["schema_version"] = "1.3.0"
    details = value["generation_details"]
    details.update(
        contract_version="hypothesis_portfolio.v5",
        response_language=None,
        stage_inputs={"GENERATOR": stage_input()},
        expected_results=[{"test_id": "t-h1", "rows": [stored_prediction()]}],
    )
    return value
