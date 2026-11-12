"""Text for a log line about an unexpected failure, without what the request carried.

The log is the only place a maintainer can see why a command failed, and people attach it to bug
reports. So the text names every exception of the chain and where it was raised, and leaves out
the things a request or a provider put into an exception message: the string values of the request
input, token-shaped secrets, and anything past the first 300 characters of a message.
"""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Iterable, Mapping, Sequence
from typing import cast

MESSAGE_LIMIT = 300
CHAIN_LIMIT = 5
FRAME_LIMIT = 25
REDACTED_INPUT = "[input]"
REDACTED_SECRET = "[secret]"
_MIN_INPUT_TEXT = 6
# Identifiers are what a maintainer searches the log for, and they are not content.
_IDENTIFIER_KEY = re.compile(r"(_id|_ids|_digest|_ref)$")
_SECRET = re.compile(
    r"sk-[A-Za-z0-9_\-]{8,}"
    r"|xai-[A-Za-z0-9_\-]{8,}"
    r"|Bearer\s+\S{8,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*"
)


def input_texts(value: object) -> tuple[str, ...]:
    """The string values of a request input that must not reach a log line, longest first.

    Values under identifier keys (thread_id, revision_digest, ...) are kept readable.
    """
    found: set[str] = set()

    def walk(item: object, key: str) -> None:
        if isinstance(item, str):
            text = item.strip()
            if len(text) >= _MIN_INPUT_TEXT and not _IDENTIFIER_KEY.search(key):
                found.add(text)
        elif isinstance(item, Mapping):
            for child_key, child in cast(Mapping[object, object], item).items():
                walk(child, str(child_key))
        elif isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray)):
            for child in cast(Sequence[object], item):
                walk(child, key)

    walk(value, "")
    return tuple(sorted(found, key=lambda text: (-len(text), text)))


def _message(exc: BaseException, redact: tuple[str, ...]) -> str:
    try:
        text = str(exc)
    except Exception:
        return "(message unavailable)"
    for hidden in redact:
        text = text.replace(hidden, REDACTED_INPUT)
    text = _SECRET.sub(REDACTED_SECRET, text)
    text = " ".join(text.split())
    if len(text) > MESSAGE_LIMIT:
        text = text[:MESSAGE_LIMIT] + "..."
    return text


def _location(filename: str) -> str:
    """A path a bug report can carry: inside the package from its root, else the last two parts."""
    parts = filename.replace("\\", "/").split("/")
    if "thoth" in parts[:-1]:
        index = len(parts) - 1 - parts[::-1].index("thoth")
        return "/".join(parts[index:])
    return "/".join(parts[-2:])


def _chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < CHAIN_LIMIT and current not in chain:
        chain.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif current.__suppress_context__:
            current = None
        else:
            current = current.__context__
    return chain


def failure_headlines(exc: BaseException, *, redact: Iterable[str] = ()) -> tuple[str, ...]:
    """One line per exception of the chain: its type and cleaned message, without frames."""
    hidden = tuple(redact)
    lines: list[str] = []
    for position, item in enumerate(_chain(exc)):
        prefix = "" if position == 0 else "caused by "
        message = _message(item, hidden)
        lines.append(f"{prefix}{type(item).__name__}{': ' + message if message else ''}")
    return tuple(lines)


def failure_text(exc: BaseException, *, redact: Iterable[str] = ()) -> str:
    """Every exception of the chain (type, cleaned message) with the frames it passed through."""
    lines: list[str] = []
    for item, headline in zip(_chain(exc), failure_headlines(exc, redact=redact), strict=True):
        lines.append(headline)
        frames = traceback.extract_tb(item.__traceback__)[-FRAME_LIMIT:]
        lines.extend(
            f"  {_location(frame.filename)}:{frame.lineno} in {frame.name}" for frame in frames
        )
    return "\n".join(lines)


class BackgroundFailureLog:
    """Warnings for failures a background task swallows, one per distinct failure and place.

    A background task that looks again every minute would otherwise write the same failure every
    minute. A place is logged again when its failure changes (the types and messages of the chain;
    the frames may differ), or after clear(place).
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._last: dict[str, tuple[str, ...]] = {}

    def note(self, place: str, exc: BaseException, *, redact: Iterable[str] = ()) -> None:
        hidden = tuple(redact)
        headlines = failure_headlines(exc, redact=hidden)
        if self._last.get(place) == headlines:
            return
        self._last[place] = headlines
        self._logger.warning(
            "background failure at %s\n%s", place, failure_text(exc, redact=hidden)
        )

    def clear(self, place: str) -> None:
        self._last.pop(place, None)
