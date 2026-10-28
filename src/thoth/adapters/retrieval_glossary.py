"""Read the Korean-to-English term list from a file in the workspace.

The file is the research list as it is delivered: a CSV with at least the columns ko, en, domain and
confidence (HIGH or MEDIUM); every other column (source, license, notes) is ignored and takes no
part in the digest. It is UTF-8, with or without a byte order mark. There is no list by default:
a missing file means no glossary, and nothing reads it unless a retrieval policy names a glossary.
"""

import csv
import io
import unicodedata
from pathlib import Path
from typing import Final

from thoth.domain.glossary import (
    MAX_ENTRIES,
    Glossary,
    GlossaryConfidence,
    GlossaryEntry,
    build_glossary,
    make_entry,
)

GLOSSARY_RELATIVE_PATH: Final = Path("retrieval-glossary") / "glossary.csv"
_REQUIRED = ("ko", "en", "domain", "confidence")
_MAX_TERM = 200


class GlossaryFileError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _collapse(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def parse_glossary_csv(text: str) -> Glossary:
    """The glossary a CSV text holds; the same entries always give the same digest."""
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff")))
    if reader.fieldnames is None or any(name not in reader.fieldnames for name in _REQUIRED):
        raise GlossaryFileError("GLOSSARY_COLUMNS_MISSING")
    entries: dict[str, GlossaryEntry] = {}
    for row in reader:
        ko, en, domain = _collapse(row["ko"]), _collapse(row["en"]), _collapse(row["domain"])
        confidence = (row["confidence"] or "").strip().upper()
        if (
            not ko
            or not en
            or not domain
            or len(ko) > _MAX_TERM
            or len(en) > _MAX_TERM
            or confidence not in ("HIGH", "MEDIUM")
        ):
            raise GlossaryFileError("GLOSSARY_ROW_INVALID")
        level: GlossaryConfidence = "HIGH" if confidence == "HIGH" else "MEDIUM"
        entry = make_entry(ko, en, domain, level)
        # The same term, translation and domain twice is one entry, at the higher confidence.
        known = entries.get(entry.entry_id)
        if known is None or (known.confidence == "MEDIUM" and level == "HIGH"):
            entries[entry.entry_id] = entry
        if len(entries) > MAX_ENTRIES:
            raise GlossaryFileError("GLOSSARY_TOO_LARGE")
    return build_glossary(tuple(entries.values()))


def load_glossary(path: Path) -> Glossary | None:
    """The glossary in a file, or None when there is no such file."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return parse_glossary_csv(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise GlossaryFileError("GLOSSARY_NOT_UTF8") from exc


class GlossaryFileReader:
    """Reads the file when it changed, so asking for the glossary on every request stays cheap."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seen: tuple[int, int] | None = None
        self._glossary: Glossary | None = None

    def __call__(self) -> Glossary | None:
        try:
            status = self._path.stat()
        except FileNotFoundError:
            self._seen, self._glossary = None, None
            return None
        stamp = (status.st_mtime_ns, status.st_size)
        if stamp != self._seen:
            self._glossary = load_glossary(self._path)
            self._seen = stamp
        return self._glossary
