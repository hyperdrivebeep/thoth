"""Protected identifiers of a question, found before any splitting at script boundaries.

The scanner reads the question once and fixes typed identifiers (paths, versions, package pins,
symbols, flags, references, quantities) by the grammar of each kind. Only the text it leaves over
is split, and only where a Latin letter or digit is directly followed by a Hangul syllable. No
particle list is used, and the question string itself is never changed: the identifiers and the
split tokens are separate fields that point back into it.

Matching against source text uses the same notion of an identifier: it ends where a letter, digit
or underscore would continue it, so a Hangul syllable after it is a boundary; a version or decimal
does not match a longer one (1.2 is not found in 1.20 or 1.2.0).
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from typing import Literal

# Bump when a grammar, the split rule or the matching rule changes; the lane version follows it.
SCANNER_VERSION = "query-identifiers:1.0.0"

type IdentifierKind = Literal[
    "PATH", "VERSION", "PACKAGE", "SYMBOL", "ENV_VAR", "REFERENCE", "FLAG", "QUANTITY", "MIXED"
]
type Script = Literal["LATIN", "DIGIT", "HANGUL"]

_EXTENSIONS = (
    "py|md|txt|json|ya?ml|toml|csv|tsv|pdf|html?|xml|sh|bat|ps1|cpp|hpp|rs|go|js|ts|java|c|h|"
    "overlay|conf|cfg|ini|log|lock|sql|ipynb|xlsx?|png|jpe?g|svg|whl|tar|gz|zip"
)
_NAME = r"[^\\/\s:*?\"<>|]"
_ASCII = re.ASCII
_UNITS = "ms|us|ns|s|kb|mb|gb|tb|khz|mhz|ghz|hz|mv|v|%"

# Priority order: at one start position the earlier grammar wins, then the longer match.
_GRAMMARS: tuple[tuple[IdentifierKind, re.Pattern[str]], ...] = (
    (
        "PATH",
        re.compile(
            rf"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:{_NAME}+[\\/])*{_NAME}*\.(?:{_EXTENSIONS})(?![A-Za-z0-9_])",
            _ASCII | re.IGNORECASE,
        ),
    ),
    (
        "PATH",
        re.compile(rf"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:{_NAME}+[\\/])*{_NAME}+", _ASCII),
    ),
    ("REFERENCE", re.compile(r"(?<![A-Za-z0-9])10\.[0-9]{4,9}/[^\s]+", _ASCII)),
    ("REFERENCE", re.compile(r"(?<![A-Za-z0-9])CVE-[0-9]{4}-[0-9]{4,7}(?![0-9])", _ASCII | re.I)),
    ("REFERENCE", re.compile(r"(?<![A-Za-z0-9])RFC ?[0-9]{3,5}(?![0-9])", _ASCII | re.I)),
    ("FLAG", re.compile(r"(?<![A-Za-z0-9_-])--[A-Za-z][A-Za-z0-9_-]*(?:=[^\s,;]+)?", _ASCII)),
    (
        "PACKAGE",
        re.compile(
            r"(?<![A-Za-z0-9_.-])[A-Za-z_][A-Za-z0-9_.-]*==[0-9][A-Za-z0-9_.]*(?![A-Za-z0-9_])",
            _ASCII,
        ),
    ),
    (
        "SYMBOL",
        re.compile(r"(?<![A-Za-z0-9_.])[A-Za-z_][A-Za-z0-9_.]*\([^()\n]{0,40}\)", _ASCII),
    ),
    (
        "PATH",
        re.compile(
            rf"(?<![A-Za-z0-9_./\\-])(?:\.{{1,2}}[\\/])?(?:[A-Za-z0-9_.@~-]+[\\/])+"
            rf"(?:[A-Za-z0-9_.@~-]*\.(?:{_EXTENSIONS})(?![A-Za-z0-9_])|(?![A-Za-z0-9_.@~-]))",
            _ASCII | re.IGNORECASE,
        ),
    ),
    (
        "PATH",
        re.compile(
            rf"(?<![A-Za-z0-9_./\\-])[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:{_EXTENSIONS})(?![A-Za-z0-9_])",
            _ASCII | re.IGNORECASE,
        ),
    ),
    (
        "ENV_VAR",
        re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?![A-Za-z0-9_])", _ASCII),
    ),
    (
        "VERSION",
        re.compile(
            r"(?<![A-Za-z0-9_.])[vV]?[0-9]+(?:\.[0-9]+)+(?:-[A-Za-z][A-Za-z0-9.]*)?(?![A-Za-z0-9_])",
            _ASCII,
        ),
    ),
    (
        "QUANTITY",
        re.compile(
            rf"(?<![A-Za-z0-9_.])(?:[0-9]+(?:\.[0-9]+)?(?:{_UNITS})|[0-9]+(?:_[0-9]+)+)(?![A-Za-z0-9_])",
            _ASCII | re.IGNORECASE,
        ),
    ),
    (
        "SYMBOL",
        re.compile(
            r"(?<![A-Za-z0-9_.@])(?:_[A-Za-z0-9_]+|[A-Za-z][A-Za-z0-9]*(?:[._@][A-Za-z0-9_]+)+)"
            r"(?![A-Za-z0-9_@])",
            _ASCII,
        ),
    ),
    (
        "MIXED",
        re.compile(
            r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9-]*[0-9])(?=[A-Za-z0-9-]*[A-Za-z])"
            r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?![A-Za-z0-9_])",
            _ASCII,
        ),
    ),
)
_RUN = re.compile(r"[A-Za-z0-9_\uAC00-\uD7A3]+")
_BOUNDARY = re.compile(r"(?<=[A-Za-z0-9_])(?=[\uAC00-\uD7A3])")
_TRAILING = ".,;:!?)]}'\""


@dataclass(frozen=True)
class QueryIdentifier:
    """One identifier the scanner fixed. `needles` run from the most to the least specific form."""

    text: str
    kind: IdentifierKind
    start: int
    end: int
    needles: tuple[str, ...]
    # The end could not be fixed from the grammar: both the written and the shorter form are kept.
    boundary_ambiguous: bool = False


@dataclass(frozen=True)
class QueryToken:
    """A piece of the text no grammar claimed, after the split at Latin/digit-to-Hangul edges."""

    text: str
    script: Script
    start: int
    end: int
    split: bool


@dataclass(frozen=True)
class QueryFields:
    """`original` is the question as asked. Offsets refer to `normalized` (NFKC of it)."""

    original: str
    normalized: str
    identifiers: tuple[QueryIdentifier, ...]
    split_tokens: tuple[QueryToken, ...]


def fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _needles(kind: IdentifierKind, text: str) -> tuple[str, ...]:
    folded = fold(text)
    if kind == "PATH":
        folded = folded.replace("\\", "/")
    if kind == "SYMBOL" and folded.endswith(")") and "(" in folded:
        name = folded.split("(", 1)[0]
        return tuple(dict.fromkeys((folded, name, name.rsplit(".", 1)[-1])))
    if kind == "SYMBOL" and "." in folded:
        return tuple(dict.fromkeys((folded, folded.rsplit(".", 1)[-1])))
    return (folded,)


def _trim(kind: IdentifierKind, text: str) -> str:
    return text.rstrip(_TRAILING) if kind in {"PATH", "REFERENCE", "FLAG"} else text


def _ambiguous_path(text: str) -> tuple[str, ...] | None:
    """A drive path with no extension whose last part has a Latin/digit-to-Hangul edge."""
    last = re.split(r"[\\/]", text)[-1]
    parts = _BOUNDARY.split(last)
    if len(parts) < 2 or "." in last:
        return None
    return (text, text[: len(text) - len(last) + len(parts[0])])


def _claim(text: str) -> list[QueryIdentifier]:
    found: list[tuple[int, int, int, QueryIdentifier]] = []
    for priority, (kind, grammar) in enumerate(_GRAMMARS):
        for match in grammar.finditer(text):
            raw = _trim(kind, match.group())
            if not raw:
                continue
            dotted = kind == "SYMBOL" and "(" not in raw and "." in raw
            if dotted and any(len(part) < 2 for part in raw.split(".")):
                continue
            written = raw
            needles = _needles(kind, raw)
            ambiguous = False
            if priority == 1:
                other = _ambiguous_path(raw)
                if other is not None:
                    ambiguous = True
                    needles = tuple(dict.fromkeys(n for p in other for n in _needles(kind, p)))
            found.append(
                (
                    match.start(),
                    priority,
                    -len(raw),
                    QueryIdentifier(
                        written, kind, match.start(), match.start() + len(raw), needles, ambiguous
                    ),
                )
            )
    found.sort(key=lambda item: item[:3])
    chosen: list[QueryIdentifier] = []
    cursor = 0
    for start, _priority, _length, identifier in found:
        if start >= cursor:
            chosen.append(identifier)
            cursor = identifier.end
    return chosen


def _script(part: str) -> Script:
    if part[0].isdigit() and part.isdigit():
        return "DIGIT"
    return "HANGUL" if "\uac00" <= part[0] <= "\ud7a3" else "LATIN"


def scan_query(question: str) -> QueryFields:
    text = unicodedata.normalize("NFKC", question)
    identifiers = _claim(text)
    ends = {identifier.end for identifier in identifiers}
    edges = [0, *(x for i in identifiers for x in (i.start, i.end)), len(text)]
    tokens: list[QueryToken] = []
    for gap_start, gap_end in zip(edges[0::2], edges[1::2], strict=True):
        for run in _RUN.finditer(text, gap_start, gap_end):
            word = run.group()
            cut = [0, *(m.start() for m in _BOUNDARY.finditer(word)), len(word)]
            for left, right in pairwise(cut):
                part = word[left:right]
                start = run.start() + left
                script = _script(part)
                after_edge = start in ends and script == "HANGUL"
                tokens.append(
                    QueryToken(part, script, start, start + len(part), len(cut) > 2 or after_edge)
                )
    return QueryFields(question, text, tuple(identifiers), tuple(tokens))


@lru_cache(maxsize=4096)
def _occurrence(needle: str, kind: IdentifierKind) -> re.Pattern[str]:
    if kind == "VERSION":
        core = needle.lstrip("v")
        return re.compile(
            r"(?<![a-z0-9_.])v?" + re.escape(core) + r"(?![a-z0-9_])(?!\.[0-9])", _ASCII
        )
    return re.compile(r"(?<![a-z0-9_])" + re.escape(needle) + r"(?![a-z0-9_])", _ASCII)


def occurs(folded_text: str, needle: str, kind: IdentifierKind) -> bool:
    """Whether the folded source text holds the needle as an identifier, not inside a longer one."""
    haystack = folded_text.replace("\\", "/") if kind == "PATH" else folded_text
    return _occurrence(needle, kind).search(haystack) is not None
