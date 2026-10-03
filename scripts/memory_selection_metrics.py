"""Read-only: compute the plan 13.3 recall metrics from the stored selection records.

Usage:
    python scripts/memory_selection_metrics.py --db <workspace>/thoth.sqlite3 --project <id> \
        [--gold gold.json]

The database is opened read-only. Metrics that need a person's answer list (gold) or a judgement
of whether an answer used a memory are `null` with the reason, never estimated. Cost (model calls,
tokens, time) is not in a selection record and is read from the model dispatch records instead.
Re-running recall with other limits (4/8/12 memories, 800/1,600/2,400 tokens) is done by building
the memory service with `limits=RecallLimits(...)`, not by this script.

Gold file: {"questions": [{"query": "<exact question text>",
                          "needed_memory_revision_ids": ["..."], "expects_no_memory": false}]}
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any

from thoth.application.services.memory_shape import is_container, member_keys
from thoth.application.services.memory_supersession import superseded_memory_digests
from thoth.domain.memory import FullMemoryContextPack, FullMemoryRevision

NEEDS_GOLD = "NEEDS_GOLD_LIST"
NEEDS_USE = "NEEDS_USED_OR_CITED"


def load_records(
    db: Path, project_id: str
) -> tuple[list[FullMemoryContextPack], list[FullMemoryRevision], dict[str, Mapping[str, object]]]:
    """Context packs, memory versions and, per version, the content of the record it points at."""

    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as connection:
        packs = [
            FullMemoryContextPack.model_validate_json(row[0])
            for row in connection.execute(
                "SELECT content_json FROM memory_context_packs WHERE project_id = ?", (project_id,)
            )
        ]
        revisions = [
            FullMemoryRevision.model_validate_json(row[0])
            for row in connection.execute(
                "SELECT content_json FROM memory_revision_ledger WHERE project_id = ?",
                (project_id,),
            )
        ]
        contents: dict[str, Mapping[str, object]] = {}
        for revision in revisions:
            found = connection.execute(
                "SELECT s.content_json FROM semantic_revisions r JOIN entity_snapshots s "
                "ON s.snapshot_id = r.snapshot_id WHERE r.revision_digest = ? AND r.project_id = ?",
                (revision.owner_revision_ref, project_id),
            ).fetchone()
            if found is not None:
                contents[revision.memory_revision_id] = json.loads(found[0])
    packs.sort(key=lambda pack: pack.created_at)
    return packs, revisions, contents


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def _recall_metrics(
    pack: FullMemoryContextPack, gold: Mapping[str, Any] | None
) -> dict[str, object]:
    selection = pack.selection
    included: set[str] = set(selection.context_included) if selection else set()
    retrieved: set[str] = set(selection.retrieved) if selection else set()
    if gold is None:
        return {
            name: {"value": None, "reason": NEEDS_GOLD}
            for name in (
                "needed_retrieval_rate",
                "needed_context_rate",
                "unneeded_retrieval_rate",
                "needed_missing",
                "abstention_fit",
            )
        }
    needed = set(gold.get("needed_memory_revision_ids", ()))
    return {
        "needed_retrieval_rate": {"value": _rate(len(retrieved & needed), len(needed))},
        "needed_context_rate": {"value": _rate(len(included & needed), len(needed))},
        "unneeded_retrieval_rate": {"value": _rate(len(retrieved - needed), len(retrieved))},
        "needed_missing": {"value": len(needed - included)},
        "abstention_fit": {
            "value": (not included) if gold.get("expects_no_memory") is True else None
        },
    }


def compute_metrics(
    packs: Iterable[FullMemoryContextPack],
    revisions: Iterable[FullMemoryRevision],
    contents: Mapping[str, Mapping[str, object]],
    gold: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, object]:
    stored = list(revisions)
    by_id = {item.memory_revision_id: item for item in stored}
    replaced = superseded_memory_digests(stored)
    containers = {
        item.memory_revision_id
        for item in stored
        if item.memory_revision_id in contents and is_container(contents[item.memory_revision_id])
    }
    rows: list[dict[str, object]] = []
    for pack in packs:
        selection = pack.selection
        included = list(selection.context_included) if selection else []
        sources = {by_id[i].source_ref for i in included if i in by_id}
        overlapping = [
            i
            for i in included
            if i in containers and member_keys(contents[i]) & {s for s in sources if s}
        ]
        rows.append(
            {
                "context_pack_id": pack.context_pack_id,
                "thread_id": pack.thread_id,
                "query": pack.query,
                "injection_enabled": True if selection is None else selection.injection_enabled,
                "counts": {
                    "eligible": 0 if selection is None else len(selection.eligible),
                    "retrieved": 0 if selection is None else len(selection.retrieved),
                    "selected": 0 if selection is None else len(selection.selected),
                    "context_included": len(included),
                    "omitted_by_budget": pack.excluded_reason_counts.get("OMITTED_BY_BUDGET", 0),
                },
                "excluded_reason_counts": dict(sorted(pack.excluded_reason_counts.items())),
                "memory_tokens": 0 if selection is None else selection.estimated_tokens,
                "superseded_included": sum(
                    1 for i in included if i in by_id and by_id[i].revision_digest in replaced
                ),
                "container_included": sum(1 for i in included if i in containers),
                "container_with_member_included": len(overlapping),
                "contradiction_pairs_included": {"value": None, "reason": "NEEDS_RELATION_LABELS"},
                "used_rate": {"value": None, "reason": NEEDS_USE},
                "answer_source_link_rate": {"value": None, "reason": NEEDS_USE},
                **_recall_metrics(pack, None if gold is None else gold.get(pack.query)),
            }
        )
    return {
        "schema_version": "1.0.0",
        "questions": rows,
        "totals": {
            "questions": len(rows),
            "memory_tokens": sum(int(str(r["memory_tokens"])) for r in rows),
            "superseded_included": sum(int(str(r["superseded_included"])) for r in rows),
            "container_included": sum(int(str(r["container_included"])) for r in rows),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--gold", type=Path)
    args = parser.parse_args(argv)
    gold: dict[str, Mapping[str, Any]] | None = None
    if args.gold is not None:
        raw = json.loads(args.gold.read_text(encoding="utf-8"))
        gold = {item["query"]: item for item in raw["questions"]}
    packs, revisions, contents = load_records(args.db, args.project)
    sys.stdout.write(
        json.dumps(compute_metrics(packs, revisions, contents, gold), ensure_ascii=False, indent=2)
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
