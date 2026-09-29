from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

import orjson

from thoth.application.services.field_execution_tools import anonymize_field_export


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("field export input must be an object")
    result = anonymize_field_export(
        {str(key): child for key, child in cast(dict[object, object], value).items()}
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
