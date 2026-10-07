"""Check the synthetic radar demo folder (invented data, never measurements).

Usage:
    python scripts/validate_synthetic_radar_demo.py [--root DIR] [--write-manifest]

The default root is examples/synthetic-radar-demo-v1.

It checks (1) every document against schema/synthetic-radar-demo-v1.schema.json, (2) the notice
line, (3) that rates are recomputed from the trial CSVs and equal the YAML summaries, (4) that no id
repeats, (5) that the scorer file only names known criteria, and (6) per-file SHA-256 against the
manifest. The manifest is an integrity check ("these files were not changed"); it says nothing
about whether the data is true or approved.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

NOTICE = "SYNTHETIC DEMO DATA - NOT MEASURED - NOT APPROVED - NOT FOR ENGINEERING USE"
MANIFEST_NAME = "00_MANIFEST_SYNTHETIC.yaml"
SCHEMA_RELATIVE = Path("schema") / "synthetic-radar-demo-v1.schema.json"
SCORER_RELATIVE = (
    Path("..") / "synthetic-radar-demo-v1-scorer" / "40_ASSESSMENT_DRAFT_SYNTHETIC.yaml"
)
INTEGRITY_STATEMENT = (
    "The SHA-256 values below only show that these files have not changed since the manifest was "
    "written. They do not show that the data is true, measured, approved or fit for any use."
)
HASH_SCOPE = "Raw file bytes (files are stored with LF line endings). This manifest is not hashed."
RATE_QUANTUM = Decimal("0.01")
CSV_COLUMNS = [
    "synthetic_notice",
    "row_kind",
    "row_id",
    "window_id",
    "detected",
    "observation_minutes",
]


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return False


def check_schema(
    value: Any, schema: dict[str, Any], root: dict[str, Any], where: str = "$"
) -> list[str]:
    """Check the small JSON-Schema subset used by the demo schema."""
    if "$ref" in schema:
        target: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return check_schema(value, target, root, where)
    problems: list[str] = []
    if "const" in schema and value != schema["const"]:
        return [f"{where}: expected {schema['const']!r}, got {value!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: {value!r} is not one of {schema['enum']}"]
    if "type" in schema and not _type_ok(value, schema["type"]):
        return [f"{where}: expected {schema['type']}, got {type(value).__name__}"]
    if "pattern" in schema and not re.search(schema["pattern"], value):
        problems.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if "oneOf" in schema:
        passing = [
            index
            for index, option in enumerate(schema["oneOf"])
            if not check_schema(value, option, root, where)
        ]
        if len(passing) != 1:
            problems.append(f"{where}: matches {len(passing)} document shapes, expected exactly 1")
            problems.extend(_closest_shape_problems(value, schema["oneOf"], root, where))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{where}: missing {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            problems.extend(
                f"{where}: unexpected {key!r}" for key in value if key not in properties
            )
        for key, sub in properties.items():
            if key in value:
                problems.extend(check_schema(value[key], sub, root, f"{where}.{key}"))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            problems.append(f"{where}: fewer than {schema['minItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                problems.extend(check_schema(item, schema["items"], root, f"{where}[{index}]"))
    return problems


def _closest_shape_problems(
    value: Any, options: list[dict[str, Any]], root: dict[str, Any], where: str
) -> list[str]:
    """Report the problems of the shape named by the document field, to make the error readable."""
    name = value.get("document") if isinstance(value, dict) else None
    for option in options:
        resolved = root["$defs"][option["$ref"].rsplit("/", 1)[-1]] if "$ref" in option else option
        if resolved.get("properties", {}).get("document", {}).get("const") == name:
            return check_schema(value, resolved, root, where)
    return []


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def rate(numerator: int, denominator: int) -> Decimal:
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        RATE_QUANTUM, rounding=ROUND_HALF_UP
    )


def read_trials(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    problems: list[str] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != CSV_COLUMNS:
            problems.append(f"{path.name}: columns are {reader.fieldnames}, expected {CSV_COLUMNS}")
        rows = list(reader)
    for number, row in enumerate(rows, start=2):
        if row.get("synthetic_notice") != NOTICE:
            problems.append(f"{path.name}:{number}: first column is not the synthetic notice")
    return rows, problems


def recompute_result(root: Path, result: dict[str, Any], where: str) -> list[str]:
    """Recompute detection and false-track numbers from the trial CSV and compare with the YAML."""
    csv_path = root / result["trials_csv"]
    if not csv_path.is_file():
        return [f"{where}: trials file {result['trials_csv']} not found"]
    rows, problems = read_trials(csv_path)
    trials = [row for row in rows if row["row_kind"] == "TARGET_TRIAL"]
    windows = [row for row in rows if row["row_kind"] == "OBSERVATION_WINDOW"]
    events = [row for row in rows if row["row_kind"] == "FALSE_TRACK_EVENT"]
    unknown = {row["row_kind"] for row in rows} - {
        "TARGET_TRIAL",
        "OBSERVATION_WINDOW",
        "FALSE_TRACK_EVENT",
    }
    problems.extend(f"{where}: unknown row_kind {kind!r}" for kind in sorted(unknown))
    window_ids = {row["row_id"] for row in windows}
    problems.extend(
        f"{where}: event {row['row_id']} points at unknown window {row['window_id']!r}"
        for row in events
        if row["window_id"] not in window_ids
    )
    detection = result["detection"]
    detected = sum(1 for row in trials if row["detected"] == "1")
    minutes = sum(int(row["observation_minutes"]) for row in windows)
    expected_detection = (detected, len(trials), rate(detected, len(trials)) if trials else None)
    given_detection = (
        detection["numerator"],
        detection["denominator"],
        Decimal(detection["value"]),
    )
    if expected_detection != given_detection:
        problems.append(
            f"{where}: detection recomputed {expected_detection}, YAML says {given_detection}"
        )
    false_track = result["false_track"]
    expected_false = (len(events), minutes, rate(len(events), minutes) if minutes else None)
    given_false = (
        false_track["numerator"],
        false_track["denominator"],
        Decimal(false_track["value"]),
    )
    if expected_false != given_false:
        problems.append(
            f"{where}: false-track recomputed {expected_false}, YAML says {given_false}"
        )
    if false_track["observation_minutes"] != minutes:
        problems.append(
            f"{where}: observation_minutes {false_track['observation_minutes']} != {minutes}"
        )
    if sorted(false_track["event_ids"]) != sorted(row["row_id"] for row in events):
        problems.append(f"{where}: event_ids differ from the false-track rows in the CSV")
    return problems


def duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def validate_scorer(
    root: Path, criteria: dict[str, dict[str, Any]], results: list[dict[str, Any]]
) -> list[str]:
    path = (root / SCORER_RELATIVE).resolve()
    if not path.is_file():
        return [f"scorer file not found: {SCORER_RELATIVE.as_posix()}"]
    scorer = read_yaml(path)
    problems: list[str] = []
    if scorer.get("notice") != NOTICE:
        problems.append("scorer: notice is missing or different")
    test_cases = {result["test_case"] for result in results}
    for phase, body in scorer.get("phases", {}).items():
        for criterion in body.get("criteria", {}):
            if criterion not in criteria:
                problems.append(f"scorer phase {phase}: unknown criterion {criterion}")
        problems.extend(
            f"scorer phase {phase}: unknown test case {case}"
            for case in body.get("available_results", [])
            if case not in test_cases and case != "SYN-TC-FOG"
        )
    return problems


def file_digests(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(
        item for item in root.rglob("*") if item.is_file() and item.name != MANIFEST_NAME
    ):
        data = path.read_bytes()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    return entries


def manifest_text(entries: list[dict[str, Any]]) -> str:
    lines = [
        f"# {NOTICE}",
        f"notice: {NOTICE}",
        "identity: THOTH-SYNTH-RADAR-DEMO-v1",
        "document: manifest",
        f"integrity_statement: {json.dumps(INTEGRITY_STATEMENT)}",
        f"hash_scope: {json.dumps(HASH_SCOPE)}",
        "files:",
    ]
    lines.extend(
        f'  - {{path: "{entry["path"]}", sha256: "{entry["sha256"]}", bytes: {entry["bytes"]}}}'
        for entry in entries
    )
    return "\n".join(lines) + "\n"


def validate_manifest(root: Path) -> list[str]:
    path = root / MANIFEST_NAME
    if not path.is_file():
        return [f"{MANIFEST_NAME} not found"]
    recorded = {entry["path"]: entry for entry in read_yaml(path).get("files", [])}
    actual = {entry["path"]: entry for entry in file_digests(root)}
    problems = [
        f"manifest: {name} is not listed" for name in sorted(actual.keys() - recorded.keys())
    ]
    problems += [
        f"manifest: {name} is listed but missing"
        for name in sorted(recorded.keys() - actual.keys())
    ]
    problems += [
        f"manifest: {name} changed since the manifest was written"
        for name in sorted(recorded.keys() & actual.keys())
        if recorded[name] != actual[name]
    ]
    return problems


def validate(root: Path) -> list[str]:
    """Return every problem found; an empty list means the folder is consistent."""
    root = root.resolve()
    schema = json.loads((root / SCHEMA_RELATIVE).read_text(encoding="utf-8"))
    problems: list[str] = []
    documents: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for path in sorted(root.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith(f"# {NOTICE}"):
            problems.append(f"{path.name}: first line is not the synthetic notice")
        data = read_yaml(path)
        problems.extend(check_schema(data, schema, schema, path.name))
        if isinstance(data, dict):
            documents.setdefault(str(data.get("document")), []).append((path.name, data))
    requirements = [data for _, data in documents.get("requirements", [])]
    test_cases = [
        case for _, data in documents.get("test_plan", []) for case in data.get("test_cases", [])
    ]
    results = [data for _, data in documents.get("result", [])]
    criteria = {
        criterion["id"]: criterion
        for data in requirements
        for criterion in data["requirement"]["criteria"]
    }
    ids = [data["requirement"]["id"] for data in requirements] + list(criteria)
    ids += [case["id"] for case in test_cases]
    for name, result in documents.get("result", []):
        problems.extend(_check_result_links(name, result, criteria, test_cases))
        problems.extend(recompute_result(root, result, name))
        csv_path = root / result["trials_csv"]
        if csv_path.is_file():
            ids += [row["row_id"] for row in read_trials(csv_path)[0]]
    problems.extend(f"duplicate id {value}" for value in duplicates(ids))
    problems.extend(validate_scorer(root, criteria, results))
    problems.extend(validate_manifest(root))
    return problems


def _check_result_links(
    name: str,
    result: dict[str, Any],
    criteria: dict[str, dict[str, Any]],
    test_cases: list[dict[str, Any]],
) -> list[str]:
    problems: list[str] = []
    case = next((item for item in test_cases if item["id"] == result["test_case"]), None)
    if case is None:
        problems.append(f"{name}: unknown test case {result['test_case']}")
    for key in ("detection", "false_track"):
        criterion = criteria.get(result[key]["criterion"])
        if criterion is None:
            problems.append(f"{name}: unknown criterion {result[key]['criterion']}")
            continue
        if criterion["unit"] != result[key]["unit"]:
            problems.append(
                f"{name}: unit {result[key]['unit']} differs from the criterion's "
                f"{criterion['unit']}"
            )
        if criterion["condition"] != result["condition"]:
            problems.append(f"{name}: condition differs from criterion {criterion['id']}")
        if case is not None and criterion["id"] not in case["covers"]:
            problems.append(f"{name}: test case {case['id']} does not cover {criterion['id']}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=Path("examples/synthetic-radar-demo-v1"))
    parser.add_argument(
        "--write-manifest", action="store_true", help="rewrite the manifest from the files"
    )
    args = parser.parse_args(argv)
    if args.write_manifest:
        root = args.root.resolve()
        (root / MANIFEST_NAME).write_text(
            manifest_text(file_digests(root)), encoding="utf-8", newline="\n"
        )
    problems = validate(args.root)
    for problem in problems:
        print(f"PROBLEM {problem}", file=sys.stderr)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
