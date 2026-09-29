from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import cast

import orjson

from thoth.application.services.field_execution_tools import summarize_session_events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--ended-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assignment_value = json.loads(args.assignment.read_text(encoding="utf-8"))
    if not isinstance(assignment_value, dict):
        raise ValueError("assignment must be an object")
    assignment = {
        str(key): child for key, child in cast(dict[object, object], assignment_value).items()
    }
    events: list[dict[str, object]] = []
    for line in args.events.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("each event must be an object")
        events.append({str(key): child for key, child in cast(dict[object, object], value).items()})
    summary = summarize_session_events(
        tuple(events),
        started_at=datetime.fromisoformat(args.started_at.replace("Z", "+00:00")),
        ended_at=datetime.fromisoformat(args.ended_at.replace("Z", "+00:00")),
    )
    output: dict[str, object] = {
        "assignment_digest": assignment.get("assignment_digest"),
        "summary": summary,
        "external_participant_session": "NOT_RUN" if not events else "LOCAL_REPLAY_ONLY",
        "d6_claimed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(orjson.dumps(output, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2))
    print(orjson.dumps(output, option=orjson.OPT_SORT_KEYS).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
