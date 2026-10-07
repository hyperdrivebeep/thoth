"""Recall evaluation by question family, with no model call.

A family is a set of questions that ask for the same thing and have the same fixed answer. Each
family has its own phrasings (the original wording, another wording, a short follow-up) and a gold
answer written before any question is run: the memories that are needed, the memories that must
never be given, whether the question can be answered at all, and whether a correction applies.

families.jsonl holds one family per line:

    {"family_id": "F001", "type": "DIRECT", "project": "project:example", "split": "dev",
     "asked_at": "2026-10-01T09:00:00+00:00",
     "phrasings": [{"id": "original", "text": "..."}, {"id": "other", "text": "..."},
                   {"id": "elliptic", "text": "..."}],
     "gold": {"needed": ["memory:abc"], "forbidden": ["rev:old"], "answerable": true,
              "correction_applied": false}}

A memory is named by its memory_id or its memory_revision_id. asked_at is optional (the moment after
the last stored memory is used). split is optional when a split.json ({"dev": [...], "sealed":
[...]}) is given. The words a model would add to a question come from a cache file
({"<sha256 of the question>": {"synonyms": [...], "keywords": [...], ...}}) that is made outside
this script; nothing here calls a model. A question missing from the cache is left out of the
"expansion" condition and counted, never silently treated as answered.

Three conditions are run: "off" (no memory is given), "current" (the present recall rules) and
"expansion" (the present rules plus the cached added words). Results are given for all families
and for each type, never only as an average. No score or weight is made.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError, model_validator

from thoth.domain.base import DomainModel
from thoth.domain.memory import FullMemoryRevision
from thoth.domain.memory_expansion import QueryExpansion

FamilyType = Literal[
    "DIRECT",
    "REPHRASED",
    "ELLIPTIC_FOLLOWUP",
    "CORRECTION",
    "TEMPORAL",
    "MULTI_EVIDENCE",
    "IRRELEVANT",
    "UNANSWERABLE",
]
TYPES: tuple[str, ...] = (
    "DIRECT",
    "REPHRASED",
    "ELLIPTIC_FOLLOWUP",
    "CORRECTION",
    "TEMPORAL",
    "MULTI_EVIDENCE",
    "IRRELEVANT",
    "UNANSWERABLE",
)
CONDITIONS: tuple[str, ...] = ("off", "current", "expansion")
# A type that asks for no stored memory: its gold names none.
_NO_MEMORY_TYPES = frozenset({"IRRELEVANT", "UNANSWERABLE"})


class FamilyError(ValueError):
    """The families file or the split file is not in the agreed form."""


class Phrasing(DomainModel):
    id: str = Field(min_length=1, max_length=60)
    text: str = Field(min_length=1, max_length=4_000)


class Gold(DomainModel):
    needed: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    answerable: bool = True
    correction_applied: bool = False

    @model_validator(mode="after")
    def _disjoint(self) -> Gold:
        if set(self.needed) & set(self.forbidden):
            raise ValueError("a memory cannot be both needed and forbidden")
        return self


class Family(DomainModel):
    family_id: str = Field(min_length=1, max_length=80)
    type: FamilyType
    project: str = Field(min_length=1, max_length=160)
    split: Literal["dev", "sealed"] | None = None
    asked_at: datetime | None = None
    phrasings: tuple[Phrasing, ...] = Field(min_length=1)
    gold: Gold

    @model_validator(mode="after")
    def _consistent(self) -> Family:
        ids = [item.id for item in self.phrasings]
        if len(set(ids)) != len(ids):
            raise ValueError("phrasing ids must be different within a family")
        if self.type == "IRRELEVANT" and self.gold.needed:
            raise ValueError("an IRRELEVANT family needs no memory")
        if self.type not in _NO_MEMORY_TYPES and not self.gold.needed:
            raise ValueError("this family type needs at least one needed memory")
        if self.type == "CORRECTION" and not self.gold.correction_applied:
            raise ValueError("a CORRECTION family says a correction applies")
        return self


def parse_families(text: str) -> list[Family]:
    families: list[Family] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            family = Family.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise FamilyError(f"line {number}: {exc}") from exc
        if family.family_id in seen:
            raise FamilyError(f"line {number}: family_id {family.family_id} is used twice")
        seen.add(family.family_id)
        families.append(family)
    return families


def select_split(
    families: Sequence[Family], split: str, split_file: Mapping[str, Sequence[str]] | None
) -> list[Family]:
    """The families of one split: by the split file when there is one, else by each family's own."""
    if split not in {"dev", "sealed"}:
        raise FamilyError("split must be dev or sealed")
    if split_file is not None:
        named = {family_id for ids in split_file.values() for family_id in ids}
        both = set(split_file.get("dev", ())) & set(split_file.get("sealed", ()))
        if both:
            raise FamilyError(f"families in both splits: {sorted(both)}")
        unknown = named - {family.family_id for family in families}
        if unknown:
            raise FamilyError(f"split file names unknown families: {sorted(unknown)}")
        wanted = set(split_file.get(split, ()))
        return [family for family in families if family.family_id in wanted]
    return [family for family in families if family.split == split]


def question_key(text: str) -> str:
    """The cache key of a question: the same words in the same order give the same key."""
    normal = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()


def load_expansion_cache(raw: Mapping[str, Any]) -> dict[str, QueryExpansion]:
    return {key: QueryExpansion.model_validate(value) for key, value in raw.items()}


@dataclass
class QuestionResult:
    family_id: str
    phrasing_id: str
    type: str
    text: str
    needed: tuple[str, ...]
    included: tuple[str, ...] = ()
    needed_hit: tuple[str, ...] = ()
    unwanted: tuple[str, ...] = ()
    forbidden_injected: tuple[str, ...] = ()
    expansion_missing: bool = False
    detail: dict[str, Any] = field(default_factory=lambda: {})

    @property
    def complete(self) -> bool:
        return len(self.needed_hit) == len(self.needed)


def _ids(item: FullMemoryRevision) -> set[str]:
    return {item.memory_id, item.memory_revision_id}


def score_question(
    family: Family, phrasing: Phrasing, included: Sequence[FullMemoryRevision]
) -> QuestionResult:
    """Count one question's recall against its gold. Counts only; no score is made."""
    needed, forbidden = set(family.gold.needed), set(family.gold.forbidden)
    given = [_ids(item) for item in included]
    hit = tuple(name for name in family.gold.needed if any(name in ids for ids in given))
    unwanted = tuple(
        item.memory_id for item, ids in zip(included, given, strict=True) if not ids & needed
    )
    injected = tuple(
        item.memory_id for item, ids in zip(included, given, strict=True) if ids & forbidden
    )
    return QuestionResult(
        family_id=family.family_id,
        phrasing_id=phrasing.id,
        type=family.type,
        text=phrasing.text,
        needed=family.gold.needed,
        included=tuple(item.memory_id for item in included),
        needed_hit=hit,
        unwanted=unwanted,
        forbidden_injected=injected,
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def summarize(results: Sequence[QuestionResult]) -> dict[str, Any]:
    """The recall measures of a set of questions (the questions' families count once each)."""
    scored = [item for item in results if not item.expansion_missing]
    asking = [item for item in scored if item.needed]
    plain = [item for item in scored if not item.needed]
    given = sum(len(item.included) for item in scored)
    per_question = [len(item.needed_hit) / len(item.needed) for item in asking]
    by_family: dict[str, list[QuestionResult]] = defaultdict(list)
    for item in asking:
        by_family[item.family_id].append(item)
    return {
        "questions": len(scored),
        "questions_missing_expansion": len(results) - len(scored),
        "families": len({item.family_id for item in scored}),
        "questions_that_need_memory": len(asking),
        # Share of the needed memories that were given, over all needed memories (micro) and as the
        # mean over questions (macro).
        "needed_recall_micro": _rate(
            sum(len(item.needed_hit) for item in asking), sum(len(item.needed) for item in asking)
        ),
        "needed_recall_macro": None
        if not per_question
        else round(sum(per_question) / len(per_question), 4),
        "all_needed_given_rate": _rate(sum(item.complete for item in asking), len(asking)),
        "family_complete_rate": _rate(
            sum(all(item.complete for item in group) for group in by_family.values()),
            len(by_family),
        ),
        # Share of the given memories that are not needed (not in gold, or forbidden).
        "unwanted_share": _rate(sum(len(item.unwanted) for item in scored), given),
        "questions_with_unwanted": sum(bool(item.unwanted) for item in scored),
        "forbidden_injected": sum(len(item.forbidden_injected) for item in scored),
        # Of the questions that need no memory, the share that were given one anyway.
        "false_positive_rate": _rate(sum(bool(item.included) for item in plain), len(plain)),
        "questions_that_need_none": len(plain),
        "memories_given": given,
    }


def summarize_by_type(results: Sequence[QuestionResult]) -> dict[str, dict[str, Any]]:
    present = {item.type for item in results}
    return {
        name: summarize([item for item in results if item.type == name])
        for name in TYPES
        if name in present
    }


class PlayedQuestion(Protocol):
    """What the recall rules did with one question (the replay's own result has these)."""

    @property
    def included(self) -> Sequence[FullMemoryRevision]: ...
    @property
    def excluded(self) -> Mapping[str, int]: ...
    @property
    def follow_up_markers(self) -> Sequence[str]: ...
    @property
    def matched_by(self) -> Mapping[str, str]: ...


# (question text, the moment it is asked, the memories that exist, the added words) -> what the
# present recall rules give. Supplied by the replay script so the rules stay in one place.
Play = Callable[
    [str, datetime, Sequence[FullMemoryRevision], QueryExpansion | None], PlayedQuestion
]


@dataclass(frozen=True)
class RunSetup:
    play: Play
    cache: Mapping[str, QueryExpansion]
    conditions: tuple[str, ...] = CONDITIONS


def evaluate(
    families: Sequence[Family],
    revisions_by_project: Mapping[str, Sequence[FullMemoryRevision]],
    setup: RunSetup,
) -> dict[str, Any]:
    """Run every question of the families under each condition and report the measures."""
    per_condition: dict[str, list[QuestionResult]] = {name: [] for name in setup.conditions}
    missing: dict[str, str] = {}
    for family in families:
        revisions = revisions_by_project.get(family.project)
        if revisions is None:
            raise FamilyError(f"family {family.family_id}: no memories loaded for {family.project}")
        asked_at = family.asked_at or (
            max((item.created_at for item in revisions), default=datetime.now(UTC))
            + timedelta(microseconds=1)
        )
        for phrasing in family.phrasings:
            key = question_key(phrasing.text)
            for name in setup.conditions:
                if name == "off":
                    per_condition[name].append(score_question(family, phrasing, ()))
                    continue
                expansion = None
                if name == "expansion":
                    expansion = setup.cache.get(key)
                    if expansion is None:
                        result = score_question(family, phrasing, ())
                        result.expansion_missing = True
                        per_condition[name].append(result)
                        missing[key] = phrasing.text
                        continue
                played = setup.play(phrasing.text, asked_at, revisions, expansion)
                result = score_question(family, phrasing, played.included)
                result.detail = {
                    "excluded": dict(played.excluded),
                    "follow_up": list(played.follow_up_markers),
                    "matched_by": dict(played.matched_by),
                }
                per_condition[name].append(result)
    return {
        "conditions": {
            name: {"overall": summarize(items), "by_type": summarize_by_type(items)}
            for name, items in per_condition.items()
        },
        "questions": {
            name: [
                {
                    "family_id": r.family_id,
                    "phrasing_id": r.phrasing_id,
                    "type": r.type,
                    "included": list(r.included),
                    "needed_hit": list(r.needed_hit),
                    "needed": list(r.needed),
                    "unwanted": list(r.unwanted),
                    "forbidden_injected": list(r.forbidden_injected),
                    "expansion_missing": r.expansion_missing,
                    **r.detail,
                }
                for r in items
            ]
            for name, items in per_condition.items()
        },
        "missing_expansions": missing,
    }


def markdown(report: Mapping[str, Any]) -> str:
    columns = (
        ("needed_recall_micro", "필요 기억 회수율"),
        ("all_needed_given_rate", "모두 불러온 질문"),
        ("unwanted_share", "혼입률"),
        ("forbidden_injected", "금지 기억 주입"),
        ("false_positive_rate", "무관 질문 거짓 양성"),
        ("questions", "질문"),
    )
    lines = [
        "| 조건 | 유형 | " + " | ".join(label for _key, label in columns) + " |",
        "|---|---|" + "---|" * len(columns),
    ]
    for name, block in report["conditions"].items():
        rows = [("전체", block["overall"]), *block["by_type"].items()]
        for kind, summary in rows:
            cells = ["-" if summary[key] is None else str(summary[key]) for key, _ in columns]
            lines.append(f"| {name} | {kind} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sealed_marker(families_path: Path) -> Path:
    return families_path.with_name(families_path.name + ".sealed-opened")
