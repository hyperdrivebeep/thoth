from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import cast

from thoth.application.services.field_execution_tools import validate_sealed_baseline

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "field-validation"
PACK = ROOT / "examples" / "projectpacks" / "public-demo-membrane"


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def csv_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def main() -> None:
    manifest = json.loads((KIT / "case_manifest.public-demo.json").read_text(encoding="utf-8"))
    expected = str(manifest["projectpack_digest"])
    actual = tree_digest(PACK)
    if expected != actual:
        raise SystemExit(f"ProjectPack digest mismatch: expected={expected} actual={actual}")
    raw = csv_header(KIT / "raw_session_template.csv")
    scores = csv_header(KIT / "blind_score_template.csv")
    required_raw = {
        "session_id",
        "reviewer_pseudonym",
        "protocol_digest",
        "case_id",
        "arm",
        "active_milliseconds",
        "rpc_operation_count",
    }
    required_scores = {
        "session_id",
        "scorer_pseudonym",
        "critical_issue_detected",
        "decision_completeness_bps",
        "critical_false_acceptance",
        "unauthorized_r3",
    }
    if not required_raw.issubset(raw):
        raise SystemExit("raw session template is missing required columns")
    if not required_scores.issubset(scores):
        raise SystemExit("blind score template is missing required columns")
    forbidden_identity_columns = {"reviewer_id", "reviewer_email", "scorer_email"}
    if forbidden_identity_columns.intersection(raw) or forbidden_identity_columns.intersection(
        scores
    ):
        raise SystemExit("field templates must use pseudonymous identities")
    schema = json.loads((KIT / "event_template.jsonl").read_text(encoding="utf-8"))
    if schema.get("event_type") != "SCHEMA":
        raise SystemExit("event template must begin with a SCHEMA record")
    if "reviewer_pseudonym" not in schema.get("required_fields", ()):
        raise SystemExit("event template must require a reviewer pseudonym")
    if "RPC_OPERATION" not in schema.get("allowed_events", ()):
        raise SystemExit("event template must allow normal RPC instrumentation")
    rights = (PACK / "RIGHTS.md").read_text(encoding="utf-8")
    if "synthetic" not in rights.lower() or "redistribut" not in rights.lower():
        raise SystemExit("public demo rights statement is incomplete")
    baseline_value = json.loads((KIT / "sealed-baseline.json").read_text(encoding="utf-8"))
    if not isinstance(baseline_value, dict):
        raise SystemExit("sealed baseline must be an object")
    baseline_payload = cast(dict[str, object], baseline_value)
    case_paths = cast(dict[str, str], baseline_payload.get("case_paths", {}))
    actual_case_digests = {
        case_id: tree_digest(ROOT / relative) for case_id, relative in case_paths.items()
    }
    baseline = validate_sealed_baseline(
        baseline_payload,
        actual_case_digests=actual_case_digests,
    )
    for required in (
        "assignment-generator.py",
        "session-runner.py",
        "score-validator.py",
        "export-anonymizer.py",
        "analysis-plan.md",
    ):
        if not (KIT / required).is_file():
            raise SystemExit(f"field execution tool is missing: {required}")
    analysis_plan = (KIT / "analysis-plan.md").read_text(encoding="utf-8")
    if "NOT_RUN" not in analysis_plan or "d6_claimed = false" not in analysis_plan:
        raise SystemExit("field analysis plan must preserve external NOT_RUN and no-D6 claims")
    print(
        json.dumps(
            {
                "verdict": "PASS",
                "projectpack_digest": actual,
                "raw_columns": len(raw),
                "score_columns": len(scores),
                "external_results": "NOT_RUN",
                "sealed_baseline_digest": baseline.baseline_digest,
                "field_execution_tools": 4,
            }
        )
    )


if __name__ == "__main__":
    main()
