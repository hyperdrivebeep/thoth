from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.architecture_contract import ROOT
from scripts.architecture_gate_contract import (
    archive_receipt,
    calculate_wiki_sync_receipt_id,
    validate_preflight,
)

GATE_DIR = Path(os.environ.get("THOTH_GATE_DIR", ROOT / ".thoth" / "architecture"))
PREFLIGHT = GATE_DIR / "preflight.json"


def _allowed_path(raw: str) -> tuple[str, Path]:
    path = (ROOT / raw).resolve()
    allowed_roots = (
        (ROOT / "docs" / "verification").resolve(),
        (ROOT / "PROJECT_WIKI").resolve(),
        (ROOT / "research-briefs").resolve(),
    )
    allowed_files = {(ROOT / "AGENTS.md").resolve()}
    if path not in allowed_files and not any(
        path == root or root in path.parents for root in allowed_roots
    ):
        raise ValueError(f"wiki-sync path is outside an allowed truth surface: {raw}")
    if not path.is_file():
        raise FileNotFoundError(f"wiki-sync file is missing: {raw}")
    return Path(os.path.relpath(path, ROOT)).as_posix(), path


def _create_only(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise ValueError("wiki-sync pointer already exists with different content") from None
        return
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--updated", action="append", default=[])
    parser.add_argument("--no-change-reason")
    args = parser.parse_args()
    if bool(args.updated) == bool(args.no_change_reason):
        raise ValueError("choose updated paths or one explicit no-change reason")
    if not PREFLIGHT.is_file():
        raise FileNotFoundError("active architecture preflight is required")
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    validate_preflight(preflight, allowed_statuses=frozenset({"READY_FOR_EDIT"}))

    updated_paths: list[dict[str, object]] = []
    for raw in args.updated:
        relative, path = _allowed_path(str(raw))
        updated_paths.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        )
    draft: dict[str, object] = {
        "schema_version": "1.0.0",
        "preflight_receipt_id": preflight["preflight_receipt_id"],
        "acceptance_id": preflight["acceptance_id"],
        "state": "UPDATED" if updated_paths else "NO_CHANGE",
        "updated_paths": updated_paths,
        "no_change_reason": None if updated_paths else str(args.no_change_reason),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    receipt_id = calculate_wiki_sync_receipt_id(draft)
    payload = {**draft, "wiki_sync_receipt_id": receipt_id}
    archive_receipt(
        GATE_DIR,
        receipt_id=receipt_id,
        kind="wiki-sync",
        payload=payload,
    )
    pointer = GATE_DIR / "wiki-sync" / f"{preflight['preflight_receipt_id']}.json"
    _create_only(pointer, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
