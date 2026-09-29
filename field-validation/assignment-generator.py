from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import cast

import orjson

from thoth.application.services.field_execution_tools import (
    generate_counterbalanced_assignments,
    validate_sealed_baseline,
)


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--reviewers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    payload_value = json.loads(args.baseline.read_text(encoding="utf-8"))
    if not isinstance(payload_value, dict):
        raise ValueError("sealed baseline must be an object")
    payload = cast(dict[str, object], payload_value)
    case_paths = cast(dict[str, str], payload.get("case_paths", {}))
    actual = {case_id: tree_digest(repo / relative) for case_id, relative in case_paths.items()}
    baseline = validate_sealed_baseline(payload, actual_case_digests=actual)
    reviewers_value = json.loads(args.reviewers.read_text(encoding="utf-8"))
    if not isinstance(reviewers_value, list):
        raise ValueError("reviewers file must be a JSON string array of pseudonyms")
    reviewer_items = cast(list[object], reviewers_value)
    if not all(isinstance(item, str) for item in reviewer_items):
        raise ValueError("reviewers file must be a JSON string array of pseudonyms")
    manifest = generate_counterbalanced_assignments(
        baseline=baseline,
        reviewer_pseudonyms=tuple(cast(list[str], reviewer_items)),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(
        orjson.dumps(
            manifest.model_dump(mode="json"),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
        )
    )
    print(orjson.dumps(manifest.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
