"""Replay recall for the fixed evaluation questions under the narrowed rules, with no model call.

Usage:
    python scripts/memory_selection_replay.py --source <workspace>/db/thoth.sqlite3 \
        --project project:eval2-memory-on --extra-question-from project:eval2-memory-off \
        [--copy %TEMP%/memnarrow_eval_copy.sqlite3] [--markdown]

The source database is opened read-only and copied with SQLite's backup API; every read after that
is from the copy. For each question of the project (its first recorded context pack gives the
question text and the moment it was asked) the memories that existed then are put through the
recall rules in memory_recall (the same functions build_context uses) and compared with the
fixed answers in the table below. No score is made and nothing is written to the project.

An extra question (Q5) that was never asked of the project is asked of its memories as they stood
after the last recorded question; that is a replay on stored memories, not a recorded run.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.application.services.memory_recall import (
    RecallLimits,
    lacks_evidence,
    narrow_recall,
    plan_recall,
)
from thoth.application.services.memory_supersession import superseded_memory_digests
from thoth.domain.enums import MemoryKind
from thoth.domain.memory import FullMemoryContextPack, FullMemoryRevision, MemoryTransition

# What the fixed answers (memory-eval-gold-20261001.md, "Q1 기억과 정답 대응") call
# each automatic memory of the new-rule project, by the last part of its source reference.
GOLD_NAMES = {
    "domain": "H2",
    "confirmation": "H3",
    "source-review": "A1",
    "owner-request": "A2",
    "scope-sandbox": "A3",
}
Q2_NEEDED = ("H2", "H3", "A1", "A2")


@dataclass(frozen=True)
class Question:
    label: str
    text: str
    asked_at: datetime
    recorded: FullMemoryContextPack | None


@dataclass(frozen=True)
class Replay:
    question: Question
    pool: tuple[FullMemoryRevision, ...]
    included: tuple[FullMemoryRevision, ...]
    excluded: dict[str, int]
    common_terms: tuple[str, ...]
    follow_up_markers: tuple[str, ...]
    # The earlier rule (any one shared word) on the same moment's pool, to check the replay itself.
    earlier_rule_included: int


def copy_database(source: Path, destination: Path) -> None:
    """Copy by SQLite's backup API from a read-only connection; the original is never written."""

    if destination.exists():
        destination.unlink()
    with (
        closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as origin,
        closing(sqlite3.connect(destination)) as copy,
    ):
        origin.backup(copy)


def load(db: Path, project_id: str) -> tuple[list[FullMemoryContextPack], list[FullMemoryRevision]]:
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
    packs.sort(key=lambda pack: pack.created_at)
    revisions.sort(key=lambda item: (item.created_at, item.memory_revision_id))
    return packs, revisions


def first_pack_per_thread(packs: Sequence[FullMemoryContextPack]) -> list[FullMemoryContextPack]:
    seen: dict[str, FullMemoryContextPack] = {}
    for pack in packs:
        seen.setdefault(pack.thread_id, pack)
    return sorted(seen.values(), key=lambda pack: pack.created_at)


def allowed_at(
    revisions: Sequence[FullMemoryRevision], asked_at: datetime
) -> list[FullMemoryRevision]:
    """Memories that pass the conditions recall checks that do not need the ledger."""

    existing = [item for item in revisions if item.created_at <= asked_at]
    superseded = superseded_memory_digests(existing)
    return [
        item
        for item in existing
        if item.revision_digest not in superseded
        and item.transition == MemoryTransition.COMMIT
        and item.support_status == "SUPPORTED"
        and item.authority_status == "AUTHORITATIVE"
        and item.cutoff_valid
        and item.recall_eligible
    ]


def replay_question(
    question: Question, revisions: Sequence[FullMemoryRevision], limits: RecallLimits
) -> Replay:
    rules = allowed_at(revisions, question.asked_at)
    if question.recorded is not None and question.recorded.selection is not None:
        # The recorded pool also had the ledger conditions (current owner, freshness) applied.
        recorded = set(question.recorded.selection.eligible)
        pool = [item for item in rules if item.memory_revision_id in recorded]
    else:
        pool = rules
    query_terms = FullProjectMemoryService._tokens(question.text)  # pyright: ignore[reportPrivateUsage]
    allowed = pool
    pool = [item for item in pool if not lacks_evidence(item)]
    earlier = [item for item in allowed if not query_terms or query_terms & set(item.query_terms)]
    earlier_rule = plan_recall(earlier, question.text, query_terms, limits)
    narrowing = narrow_recall(pool, question.text, query_terms)
    plan = plan_recall(
        narrowing.relevant,
        question.text,
        query_terms,
        limits,
        follow_up=bool(narrowing.follow_up_markers),
    )
    return Replay(
        question=question,
        pool=tuple(pool),
        included=tuple(plan.included),
        excluded={reason: len(ids) for reason, ids in sorted(narrowing.excluded.items())},
        common_terms=tuple(sorted(narrowing.common_terms)),
        follow_up_markers=narrowing.follow_up_markers,
        earlier_rule_included=len(earlier_rule.included),
    )


def name_of(item: FullMemoryRevision) -> str:
    """The fixed answers' name for a memory, or what it is."""

    if item.kind == MemoryKind.LESSON:
        return "정정(LESSON)"
    tail = (item.source_ref or "").rsplit(":", 1)[-1]
    return GOLD_NAMES.get(tail, f"기타({tail})")


def judge(replay: Replay, revisions: Sequence[FullMemoryRevision]) -> tuple[bool, str]:
    names = [name_of(item) for item in replay.included]
    label = replay.question.label
    unbacked = [name_of(i) for i in replay.included if lacks_evidence(i)]
    if label == "Q2":
        hits = [name for name in Q2_NEEDED if name in names]
        found = f"필요 4건 중 {len(hits)}건({', '.join(hits)}), 근거 0개 {len(unbacked)}건"
        return len(hits) >= 3 and not unbacked, found
    if label == "Q3":
        parents = {r.parent_revision_digest for r in revisions if r.parent_revision_digest}
        old = [item for item in replay.included if item.revision_digest in parents]
        has_fix = "정정(LESSON)" in names
        return has_fix and not old, f"정정 포함 {has_fix}, 옛 H2 {len(old)}건"
    if label == "Q4":
        iris = [
            name_of(i)
            for i in replay.included
            if i.kind in (MemoryKind.HYPOTHESIS, MemoryKind.ACTION)
        ]
        return not iris, f"가설·행동 {len(iris)}건 {iris}"
    if label == "Q5":
        needed = [name for name in names if name in ("정정(LESSON)", "A1")]
        fine = {"정정(LESSON)", "A1", "H1", "A2"}
        extra = [name for name in names if name not in fine]
        ok = bool(needed) and len(extra) * 2 < len(names)
        return ok, f"정정 또는 A1 {needed}, 불필요 {len(extra)}/{len(names)}건 {extra}"
    return not names, f"{len(names)}건 포함(빈 프로젝트여야 함)"


def build_questions(
    packs: Sequence[FullMemoryContextPack],
    extra: Sequence[FullMemoryContextPack] | None,
) -> list[Question]:
    firsts = first_pack_per_thread(packs)
    questions = [
        Question(f"Q{index}", pack.query, pack.created_at, pack)
        for index, pack in enumerate(firsts, start=1)
    ]
    if extra:
        last = first_pack_per_thread(extra)[-1]
        if all(q.text != last.query for q in questions):
            questions.append(Question(f"Q{len(questions) + 1}", last.query, last.created_at, None))
    return questions


def run(
    db: Path, project: str, extra_project: str | None, limits: RecallLimits
) -> list[dict[str, Any]]:
    packs, revisions = load(db, project)
    extra = load(db, extra_project)[0] if extra_project else None
    rows: list[dict[str, Any]] = []
    for question in build_questions(packs, extra):
        replay = replay_question(question, revisions, limits)
        ok, detail = judge(replay, revisions)
        recorded = question.recorded
        rows.append(
            {
                "question": question.label,
                "text": question.text[:60].replace("\n", " "),
                "asked_at": question.asked_at.isoformat(),
                "pool": len(replay.pool),
                "included": [name_of(i) for i in replay.included],
                "recorded_included": None if recorded is None else len(recorded.included),
                "earlier_rule_replayed": replay.earlier_rule_included,
                "excluded": replay.excluded,
                "common_terms": list(replay.common_terms),
                "follow_up": list(replay.follow_up_markers),
                "criterion": detail,
                "pass": ok,
            }
        )
    return rows


def markdown(rows: Sequence[dict[str, Any]]) -> str:
    lines = [
        "| 질문 | 시점의 후보 | 이전 규칙(기록 / 재계산) | 새 규칙에서 들어간 기억 | 제외 사유 "
        "| 합격 기준 대비 | 합격 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        recorded = "기록 없음" if row["recorded_included"] is None else row["recorded_included"]
        verdict = "합격" if row["pass"] else "불합격"
        lines.append(
            f"| {row['question']} | {row['pool']} | {recorded} / {row['earlier_rule_replayed']} | "
            f"{', '.join(map(str, row['included'])) or '없음'} | {row['excluded'] or '-'} | "
            f"{row['criterion']} | {verdict} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, out: Callable[[str], object] = print) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--extra-question-from")
    parser.add_argument("--copy", type=Path)
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument(
        "--output", type=Path, help="write the result here (UTF-8) instead of printing"
    )
    options = parser.parse_args(argv)
    copy = options.copy or Path(os.environ.get("TEMP", ".")) / "memory_replay_copy.sqlite3"
    copy_database(options.source, copy)
    rows = run(copy, options.project, options.extra_question_from, RecallLimits())
    text = markdown(rows) if options.markdown else json.dumps(rows, ensure_ascii=False, indent=2)
    if options.output is not None:
        options.output.write_text(text + "\n", encoding="utf-8")
    else:
        out(text)
    return 0 if all(bool(row["pass"]) for row in rows[1:]) else 1


if __name__ == "__main__":
    sys.exit(main())
