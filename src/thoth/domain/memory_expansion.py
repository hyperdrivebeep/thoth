"""Words a model adds to a question so a memory written in other words can still be found.

The model is asked once per investigation. What it returns is only search words: it never decides
which memory is recalled, and no score is made. A memory that the question's own words reach comes
first; one reached only through added words follows as a separate group and is marked as such.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, cast

from pydantic import Field, field_validator

from thoth.domain.base import DomainModel

MAX_EXPANSION_ITEMS = 8
MAX_EXPANSION_ITEM_CHARS = 40
MAX_NOTE_LINE_CHARS = 240
# The purpose recorded on the model call, so its usage is shown apart from the investigation's.
MEMORY_QUERY_EXPANSION_PURPOSE = "MEMORY_QUERY_EXPANSION"
MEMORY_QUERY_EXPANSION_LABEL = "기억 검색어 넓히기"

ExpansionStatus = Literal["USED", "EXPANSION_FAILED", "EXPANSION_INVALID", "NOT_ASKED"]


def _clean_items(value: object) -> object:
    """Keep at most eight short, non-empty phrases; a value that is not a list is left to fail."""

    if not isinstance(value, (list, tuple)):
        return value
    items = cast(Sequence[object], value)
    kept: list[str] = []
    for item in items:
        if not isinstance(item, str):
            return items
        text = " ".join(item.split())
        if text and len(text) <= MAX_EXPANSION_ITEM_CHARS and text not in kept:
            kept.append(text)
    return tuple(kept[:MAX_EXPANSION_ITEMS])


class QueryExpansion(DomainModel):
    """The model's answer: other words for the question, its topic in another language, related
    terms, and one sentence written the way a stored memory about it might read."""

    synonyms: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    note_line: str = Field(default="", max_length=MAX_NOTE_LINE_CHARS)

    @field_validator("synonyms", "keywords", "related", mode="before")
    @classmethod
    def _limit_items(cls, value: object) -> object:
        return _clean_items(value)

    @field_validator("note_line", mode="before")
    @classmethod
    def _limit_note(cls, value: object) -> object:
        if isinstance(value, str):
            return " ".join(value.split())[:MAX_NOTE_LINE_CHARS]
        return value

    def text(self) -> str:
        return " ".join((*self.synonyms, *self.keywords, *self.related, self.note_line)).strip()


class MemoryExpansionRecord(DomainModel):
    """What happened to the question-widening step of one investigation (selection record)."""

    status: ExpansionStatus = "NOT_ASKED"
    # Why no call was made (SETTING_OFF, MEMORY_OFF, NO_CANDIDATES, FOLLOW_UP) or what went wrong.
    reason: str | None = None
    # Words added to the question's own words that took part in matching.
    added_words: tuple[str, ...] = ()
    dispatch_ids: tuple[str, ...] = ()


class MemoryExpansionOutcome(DomainModel):
    record: MemoryExpansionRecord = Field(default_factory=MemoryExpansionRecord)
    expansion: QueryExpansion | None = None

    @property
    def used(self) -> bool:
        return self.record.status == "USED" and self.expansion is not None
