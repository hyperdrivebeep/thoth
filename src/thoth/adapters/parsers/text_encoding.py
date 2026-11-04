"""Read text bytes into characters without ever keeping garbled text.

UTF-8 (with or without a BOM) is read as before. Other encodings are used only when certain:

* a byte order mark or an HTML `<meta charset>` declares the encoding and the bytes read cleanly
  with it, or
* nothing is declared, the bytes are not UTF-8, and exactly one of the legacy East Asian encodings
  reads them as clean text that looks like the language that encoding is for.

Any other case is refused with `TEXT_ENCODING_UNCONFIRMED` rather than guessed, so no source ever
holds mojibake or U+FFFD replacement characters. A valid UTF-8 file wins over a conflicting
declaration: bytes that are valid UTF-8 text are not valid text in the other encodings.
"""

from __future__ import annotations

import codecs
import re
import unicodedata
from dataclasses import dataclass

from thoth.domain.enums import ParserErrorCode
from thoth.domain.errors import ParserFailure

UTF8 = "utf-8"
REASON = "TEXT_ENCODING_UNCONFIRMED"

# Candidate encodings tried, in this order, when nothing is declared.
CANDIDATES = ("gb18030", "big5hkscs", "cp932", "euc_jp", "cp949")

# Too little text cannot tell one legacy encoding from another: the same two bytes are a different
# valid character in each of them.
MIN_LETTERS = 6

_LABELS = {
    "utf8": "utf-8",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb18030": "gb18030",
    "big5": "big5hkscs",
    "big5-hkscs": "big5hkscs",
    "shift_jis": "cp932",
    "shift-jis": "cp932",
    "sjis": "cp932",
    "x-sjis": "cp932",
    "windows-31j": "cp932",
    "euc-jp": "euc_jp",
    "euc-kr": "cp949",
    "ks_c_5601-1987": "cp949",
    "cp949": "cp949",
    "windows-949": "cp949",
}

_META = re.compile(
    rb"<meta[^>]+?charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)",
    re.IGNORECASE,
)

# The most frequent characters of ordinary Chinese prose. Real text is full of them; text that was
# read with the wrong legacy encoding is not.
COMMON_SIMPLIFIED = frozenset(
    "的一是不了人我在有他这中大来上国个到说们为子和你地出道也时年得就那要下以生会自着去之过家学对可她里后小么心多天而能好都然没日于起还发成事只作当想看文无开手十用主行方又如前所本见经头面公同三已老从动两长知民样现分将外但身些与高意进把法此实回二理美点月明其种声全工己话儿者向情部正名定女问力机给等几很业最间新什少"
)
_COMMON_TRADITIONAL = frozenset(
    "的一是不了人我在有他這中大來上國個到說們為子和你地出道也時年得就那要下以生會自著去之過家學對可她裡後小麼心多天而能好都然沒日於起還發成事只作當想看文無開手十用主行方又如前所本見經頭面公同三已老從動兩長知民樣現分將外但身些與高意進把法此實回二理點月明其種聲全工己話兒者向情部正名定女問力機給等幾很業最間新什少"
)


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str

    @property
    def warnings(self) -> tuple[str, ...]:
        """Recorded on the nodes unless it is plain UTF-8, whose results never change."""
        return () if self.encoding == UTF8 else (f"TEXT_ENCODING:{self.encoding}",)


def _refuse(detail: str) -> ParserFailure:
    return ParserFailure(ParserErrorCode.TEXT_ENCODING_UNCONFIRMED, f"{REASON}: {detail}")


def declared_html_encoding(raw: bytes) -> str | None:
    """The encoding an HTML document declares in the first bytes of its `<meta>` tags, if any."""
    found = _META.search(raw[:4096])
    return None if found is None else found.group(1).decode("ascii").lower()


def _canonical(label: str) -> str | None:
    return _LABELS.get(label.strip().lower())


def _clean(text: str) -> bool:
    for char in text:
        if char == "\ufffd":
            return False
        if char not in "\t\n\r\f" and unicodedata.category(char) in {"Cc", "Co", "Cn", "Cs"}:
            return False
    return True


def _letters(text: str) -> list[str]:
    return [c for c in text if ord(c) > 127 and unicodedata.category(c).startswith("L")]


def _share(text: str, low: int, high: int) -> float:
    letters = _letters(text)
    if not letters:
        return 0.0
    return sum(1 for c in letters if low <= ord(c) <= high) / len(letters)


def _han_share(text: str) -> float:
    letters = _letters(text)
    han = [c for c in letters if 0x3400 <= ord(c) <= 0x9FFF]
    return 0.0 if not letters else len(han) / len(letters)


def _common_share(text: str, common: frozenset[str]) -> float:
    han = [c for c in text if 0x3400 <= ord(c) <= 0x9FFF]
    return 0.0 if not han else sum(1 for c in han if c in common) / len(han)


def _plausible(encoding: str, text: str) -> bool:
    """Does the text read as the language this encoding is for, rather than as garbled bytes?"""
    if len(_letters(text)) < MIN_LETTERS:
        return False
    if encoding == "cp949":
        return _share(text, 0xAC00, 0xD7A3) >= 0.9
    if encoding in {"cp932", "euc_jp"}:
        kana = _share(text, 0x3040, 0x30FF)
        return kana >= 0.15 and kana + _han_share(text) >= 0.9
    common = _COMMON_TRADITIONAL if encoding == "big5hkscs" else COMMON_SIMPLIFIED
    return _han_share(text) >= 0.9 and _common_share(text, common) >= 0.25


def _strict(raw: bytes, encoding: str) -> str | None:
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        return None


def decode_text(raw: bytes, *, declared: str | None = None) -> DecodedText:
    """Characters and the encoding used; refuses with TEXT_ENCODING_UNCONFIRMED if uncertain."""
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = ""
    if encoding:
        text = _strict(raw, encoding)
        if text is None or not _clean(text):
            raise _refuse(f"the {encoding} byte order mark does not match the bytes")
        return DecodedText(text, encoding)
    utf8 = _strict(raw, "utf-8-sig")
    if utf8 is not None:
        return DecodedText(utf8, UTF8)
    if declared is not None:
        encoding = _canonical(declared) or ""
        if not encoding:
            raise _refuse(f"declared encoding {declared!r} is not supported")
        text = None if encoding == UTF8 else _strict(raw, encoding)
        if text is None or not _clean(text):
            raise _refuse(f"the bytes are not clean {declared} text")
        return DecodedText(text, encoding)
    fits: list[DecodedText] = []
    for candidate in CANDIDATES:
        text = _strict(raw, candidate)
        if text is not None and _clean(text) and _plausible(candidate, text):
            fits.append(DecodedText(text, candidate))
    if len(fits) != 1:
        reason = (
            "no known encoding reads these bytes cleanly"
            if not fits
            else (
                "more than one encoding reads these bytes cleanly: "
                + ", ".join(item.encoding for item in fits)
            )
        )
        raise _refuse(reason + "; save the file as UTF-8")
    return fits[0]
