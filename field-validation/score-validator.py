from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

import orjson

from thoth.application.services.field_execution_tools import adjudicate_blind_scores


def _object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"score input must be an object: {path}")
    return {str(key): child for key, child in cast(dict[object, object], value).items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--adjudicator")
    parser.add_argument("--resolution", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = adjudicate_blind_scores(
        first=_object(args.first),
        second=_object(args.second),
        adjudicator_pseudonym=args.adjudicator,
        resolution=(
            None if args.resolution is None else cast(dict[str, int], _object(args.resolution))
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(
        orjson.dumps(
            result.model_dump(mode="json"),
            option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
        )
    )
    print(orjson.dumps(result.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
