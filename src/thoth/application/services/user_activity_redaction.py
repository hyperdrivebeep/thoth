"""Safe activity values, source targets and redaction classes without persistence."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Iterable, Mapping
from typing import cast
from urllib.parse import urlsplit

from thoth.domain.user_activity import (
    UserActivityLocator,
    UserActivityRedaction,
    UserActivityTarget,
)

# Shared only by the existing event projector and terminal transformer.
__all__ = [
    "SourceActivityContext",
    "_alias",
    "_items",
    "_record",
    "_redaction",
    "_redaction_classes",
    "_safe_code",
    "_source_target",
]


SourceActivityContext = Mapping[str, object]

_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,159}$")

_CELL = re.compile(r"^\$?[A-Z]{1,4}\$?[1-9][0-9]*(?::\$?[A-Z]{1,4}\$?[1-9][0-9]*)?$")

_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")

_SECRET = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~-]{8,}|"
    r"(?:api[_-]?key|access[_-]?token|password|secret|credential|private[_-]?key|dsn)"
    r"\s*[:=]\s*\S+)"
)

_PROMPT_INJECTION = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?previous\s+instructions|system\s+prompt|"
    r"reveal\s+(?:the\s+)?prompt|execute\s+(?:this\s+)?command)"
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _record(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _items(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _alias(kind: str, value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return f"{kind}:{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _safe_code(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_CODE.fullmatch(value) else None


def _redaction_classes(value: object, *, source_content: bool = False) -> set[str]:
    classes: set[str] = set()
    if isinstance(value, Mapping):
        record = cast(Mapping[object, object], value)
        for key, item in record.items():
            lowered = str(key).casefold()
            if lowered in {"raw_command", "command", "argv", "stdout", "stderr"}:
                classes.add("raw_command")
                continue
            if lowered in {"prompt", "raw_prompt", "model_payload", "chain_of_thought"}:
                classes.add("prompt_payload")
                continue
            if lowered in {"exact_text", "excerpt", "source_text", "content"}:
                classes.add("unauthorized_source_excerpt")
                classes.update(_redaction_classes(item, source_content=True))
                continue
            if any(
                token in lowered
                for token in ("api_key", "password", "secret", "private_key", "access_token", "dsn")
            ):
                classes.add("secret")
                continue
            classes.update(_redaction_classes(item, source_content=source_content))
    elif isinstance(value, (list, tuple)):
        for item in cast(Iterable[object], value):
            classes.update(_redaction_classes(item, source_content=source_content))
    elif isinstance(value, str):
        if _CONTROL.search(value):
            classes.add("control_characters")
        if _SECRET.search(value) or "-----BEGIN PRIVATE KEY-----" in value:
            classes.add("secret")
        if _LOCAL_PATH.search(value) or value.casefold().startswith("file://"):
            classes.add("local_absolute_path")
        parsed = urlsplit(value) if "://" in value else None
        if parsed is not None and parsed.query:
            classes.add("query_token")
        if parsed is not None and (parsed.username or parsed.password):
            classes.add("secret")
        if source_content and _PROMPT_INJECTION.search(value):
            classes.add("untrusted_source_text")
    return classes


def _redaction(classes: set[str]) -> UserActivityRedaction:
    ordered = tuple(sorted(classes))
    return UserActivityRedaction(
        applied=bool(ordered),
        classes=ordered,
        public_note_ko="민감하거나 신뢰할 수 없는 상세는 숨겼습니다." if ordered else None,
    )


def _public_origin(value: object, classes: set[str]) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value:
        return None, "project-source"
    classes.update(_redaction_classes(value))
    try:
        parsed = urlsplit(value)
    except ValueError:
        classes.add("invalid_uri")
        return None, "project-source"
    try:
        host = (parsed.hostname or "").encode("idna").decode().casefold()
    except (UnicodeError, ValueError):
        classes.add("invalid_uri")
        return None, "project-source"
    if parsed.scheme not in {"http", "https"} or not host:
        if parsed.scheme or _LOCAL_PATH.search(value):
            classes.add("local_absolute_path")
        return None, "project-source"
    internal = host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal", ".lan")
    )
    try:
        internal = internal or not ipaddress.ip_address(host).is_global
    except ValueError:
        internal = internal or "." not in host
    if internal:
        classes.add("internal_host")
        return None, "project-source"
    if parsed.query:
        classes.add("query_token")
    if parsed.fragment:
        classes.add("uri_fragment")
    if parsed.username or parsed.password:
        classes.add("secret")
    return f"{parsed.scheme}://{host}", host[:120]


def _safe_section(value: object, classes: set[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    classes.update(_redaction_classes(value, source_content=True))
    text = _CONTROL.sub(" ", value).strip()
    if (
        len(text) > 120
        or _SECRET.search(text)
        or _LOCAL_PATH.search(text)
        or _PROMPT_INJECTION.search(text)
    ):
        classes.add("untrusted_source_text")
        return None
    return text


def _source_target(
    span_id: object, context: SourceActivityContext, fallback: Mapping[str, object]
) -> tuple[UserActivityTarget, set[str]]:
    classes = _redaction_classes(fallback, source_content=True)
    classes.update(_redaction_classes(context, source_content=True))
    locator = _record(context.get("locator")) or dict(fallback)
    page = locator.get("page") if isinstance(locator.get("page"), int) else None
    line = locator.get("line") if isinstance(locator.get("line"), int) else None
    cell_raw = locator.get("cell_range")
    cell = cell_raw if isinstance(cell_raw, str) and _CELL.fullmatch(cell_raw) else None
    if isinstance(cell_raw, str) and cell is None:
        classes.add("unsafe_locator")
    section = _safe_section(locator.get("section"), classes)
    safe_locator = UserActivityLocator(
        page=page if isinstance(page, int) and page >= 1 else None,
        section=section,
        cell=cell,
        line_start=line if isinstance(line, int) and line >= 1 else None,
        line_end=line if isinstance(line, int) and line >= 1 else None,
    )
    display_parts: list[str] = []
    if safe_locator.page is not None:
        display_parts.append(f"p.{safe_locator.page}")
    if safe_locator.cell is not None:
        display_parts.append(safe_locator.cell)
    if safe_locator.line_start is not None:
        display_parts.append(f"line {safe_locator.line_start}")
    safe_uri, host_alias = _public_origin(context.get("source_uri"), classes)
    cutoff = context.get("cutoff_state")
    authority = context.get("authority_state")
    currentness = (
        "current"
        if cutoff == "ELIGIBLE"
        else "unknown_time"
        if cutoff == "UNKNOWN_TIME"
        else "stale"
        if cutoff == "AFTER_CUTOFF"
        else "access_unverified"
    )
    access_state = (
        "out_of_scope"
        if cutoff == "PROHIBITED_CONTEXT" or authority == "NOT_ADMISSIBLE"
        else "allowed"
        if context
        else "unknown"
    )
    digest = context.get("text_sha256")
    short_hash = (
        str(digest)[:12]
        if isinstance(digest, str) and re.fullmatch(r"[A-Fa-f0-9]{64}", digest)
        else None
    )
    return (
        UserActivityTarget(
            kind="span",
            title="연결된 자료",
            display_ref=" · ".join(display_parts) or None,
            host_alias=host_alias,
            safe_uri=safe_uri,
            locator=safe_locator,
            source_version_id=_alias("source-version", context.get("source_version_id")),
            hash_short=short_hash,
            currentness=currentness,
            access_state=access_state,
        ),
        classes,
    )
