"""thoth-trace-csv/1: the trace as one CSV file a spreadsheet can open, and the way back.

One file holds rows of five kinds (ITEM, LINK, RULE, RESULT, DELETE). Every row has every column;
the order of the columns and of the rows does not matter, blank lines are skipped, and a row is
found by its id, never by its position. Cells that a spreadsheet would run as a formula (they begin
with = + - @ tab or CR) are written with a leading apostrophe and read back by removing exactly
that one apostrophe.

One column is optional: synthetic_notice. A set whose items carry the notice field (invented demo
data) is exported with that column filled on every row, so the mark is seen in a spreadsheet and
comes back on import; a file without the column is still a complete file.

This module only turns a trace set into text and text into checked rows. Deciding what an import
changes is in verification_trace_import.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from thoth.application.services.trace_closure_text import CLOSURE_COLUMN, with_closure_notes
from thoth.domain.canonical import normalize_timestamp
from thoth.domain.verification_trace import (
    Comparator,
    CriterionRule,
    ResultRecord,
    Rounding,
    TraceItem,
    TraceKind,
    TraceLink,
    TraceRelation,
    TraceSet,
)

FORMAT = "thoth-trace-csv/1"
BOM = "\ufeff"
MAX_CSV_CHARS = 5_000_000
MAX_ROWS = 20_000

COLUMNS: tuple[str, ...] = (
    "format",
    "row_type",
    "base_set_digest",
    "item_id",
    "link_id",
    "rule_id",
    "result_id",
    "target_kind",
    "target_id",
    "item_key",
    "kind",
    "title",
    "fields_json",
    "source_span_refs",
    "from_id",
    "to_id",
    "relation",
    "criterion_id",
    "rule_revision",
    "measure",
    "comparator",
    "threshold",
    "unit",
    "condition",
    "rounding_digits",
    "rounding_mode",
    "required",
    "result_revision",
    "value",
    "raw_value",
    "numerator",
    "denominator",
    "observed_at",
)
ROW_TYPES = ("ITEM", "LINK", "RULE", "RESULT", "DELETE")
DELETE_KINDS = ("ITEM", "LINK", "RULE", "RESULT")
NOTICE_COLUMN = "synthetic_notice"
NOTICE_FIELD = "synthetic_notice"  # the item field the column stands for

# What the file cannot carry. Exported with every file so nobody assumes the CSV is the whole trace.
LOSS_MANIFEST: tuple[str, ...] = (
    "evidence_text: only the positions of evidence (source_span_refs) are kept, not the text",
    "verdict_history: computed verdicts and their revisions are recomputed after an import",
    "confirmations: who confirmed which verdict stays in THOTH and is not in the file",
    "selection_policy: the policy that picks among results stays as set in THOTH",
    "pending_changes: which verdicts are out of date is worked out again by THOTH",
    "closures: closure_status is for reading only; editing it changes nothing. "
    "Record closures in THOTH.",
)

_ESCAPE_NEEDED = re.compile(r"^'*[=+\-@\t\r]")
_ESCAPED = re.compile(r"^'+[=+\-@\t\r]")


@dataclass(frozen=True)
class Issue:
    code: str
    row: int | None = None
    ref: str | None = None
    detail: str = ""

    def as_json(self) -> dict[str, Any]:
        return {"code": self.code, "row": self.row, "ref": self.ref, "detail": self.detail}


@dataclass(frozen=True)
class ParsedRow:
    row: int  # the line of the file, only for messages; it is never an id
    row_type: str
    key: str
    model: BaseModel | None = None
    target_kind: str | None = None
    key_given: bool = True  # an item row may leave its internal key blank


@dataclass
class ParsedCsv:
    rows: list[ParsedRow] = field(default_factory=lambda: list[ParsedRow]())
    issues: list[Issue] = field(default_factory=lambda: list[Issue]())
    base_digests: tuple[str, ...] = ()
    ignored_columns: tuple[str, ...] = ()
    reference_ids: set[str] = field(default_factory=lambda: set[str]())


def input_sha256(text: str) -> str:
    """Hash of the exact text that was sent, byte order mark included."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def escape_cell(text: str) -> str:
    return "'" + text if _ESCAPE_NEEDED.match(text) else text


def unescape_cell(text: str) -> str:
    return text[1:] if _ESCAPED.match(text) else text


# --- export --------------------------------------------------------------------------------------


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _opt(value: object) -> str:
    return "" if value is None else str(value)


def _decimal(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_KIND_ORDER = {kind: number for number, kind in enumerate(TraceKind)}


def export_rows(trace_set: TraceSet) -> list[dict[str, str]]:
    base = {"format": FORMAT, "base_set_digest": trace_set.set_digest}
    notice = next(
        (
            item.fields[NOTICE_FIELD]
            for item in sorted(trace_set.items, key=lambda i: i.item_id)
            if item.fields.get(NOTICE_FIELD)
        ),
        "",
    )
    if notice:
        base[NOTICE_COLUMN] = notice  # every row of a synthetic set carries the mark
    rows: list[dict[str, str]] = []
    for item in sorted(trace_set.items, key=lambda i: (_KIND_ORDER[i.kind], i.item_id)):
        shown = {name: text for name, text in item.fields.items() if name != NOTICE_FIELD}
        rows.append(
            {
                **base,
                "row_type": "ITEM",
                "item_id": item.item_id,
                "item_key": item.item_key,
                "kind": item.kind.value,
                "title": item.title,
                "fields_json": _json(shown) if shown else "",
                "source_span_refs": _json(list(item.source_span_refs))
                if item.source_span_refs
                else "",
                **({NOTICE_COLUMN: item.fields.get(NOTICE_FIELD, "")} if notice else {}),
            }
        )
    for link in sorted(trace_set.links, key=lambda i: i.link_id):
        rows.append(
            {
                **base,
                "row_type": "LINK",
                "link_id": link.link_id,
                "from_id": link.from_id,
                "to_id": link.to_id,
                "relation": link.relation.value,
            }
        )
    for rule in sorted(trace_set.rules, key=lambda i: i.rule_id):
        rows.append(
            {
                **base,
                "row_type": "RULE",
                "rule_id": rule.rule_id,
                "rule_revision": str(rule.rule_revision),
                "criterion_id": rule.criterion_id,
                "measure": rule.measure,
                "comparator": rule.comparator.value,
                "threshold": _decimal(rule.threshold),
                "unit": rule.unit,
                "condition": rule.condition,
                "rounding_digits": str(rule.rounding.digits),
                "rounding_mode": rule.rounding.mode,
                "required": _bool(rule.required),
            }
        )
    for result in sorted(trace_set.results, key=lambda i: i.result_id):
        rows.append(
            {
                **base,
                "row_type": "RESULT",
                "result_id": result.result_id,
                "result_revision": str(result.result_revision),
                "criterion_id": result.criterion_id,
                "condition": result.condition,
                "value": _decimal(result.value),
                "raw_value": _opt(result.raw_value),
                "unit": result.unit,
                "numerator": _opt(result.numerator),
                "denominator": _opt(result.denominator),
                "observed_at": normalize_timestamp(result.observed_at),
                "source_span_refs": _json(list(result.source_span_refs))
                if result.source_span_refs
                else "",
            }
        )
    return rows


def write_csv(rows: Iterable[dict[str, str]]) -> str:
    """Rows as text with a byte order mark (so a spreadsheet reads the Korean text correctly)."""
    rows = list(rows)
    columns = (*COLUMNS, *(n for n in (NOTICE_COLUMN, CLOSURE_COLUMN) if any(n in r for r in rows)))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([escape_cell(row.get(name, "")) for name in columns])
    return BOM + buffer.getvalue()


def export_csv(trace_set: TraceSet, closures: Mapping[str, str] | None = None) -> str:
    return write_csv(with_closure_notes(export_rows(trace_set), closures))


# --- parse ---------------------------------------------------------------------------------------


def _guard(issues: list[Issue], row: int, ref: str, build: Callable[[], Any]) -> Any:
    try:
        return build()
    except (ValueError, ValidationError, InvalidOperation, KeyError, TypeError) as exc:
        message = str(exc).splitlines()
        issues.append(Issue("ROW_INVALID", row, ref, (message[-1] if message else "")[:200]))
        return None


def _decimal_in(text: str) -> Decimal:
    value = Decimal(text.strip())
    if not value.is_finite():
        raise ValueError("number must be finite")
    return value


def _int_or_none(text: str) -> int | None:
    return None if not text.strip() else int(text.strip())


def _span_refs(text: str) -> tuple[str, ...]:
    if not text.strip():
        return ()
    loaded: object = json.loads(text)
    if not isinstance(loaded, list):
        raise ValueError("source_span_refs must be a JSON list of text")
    parts = cast(list[object], loaded)
    if not all(isinstance(part, str) for part in parts):
        raise ValueError("source_span_refs must be a JSON list of text")
    return tuple(cast(list[str], parts))


def _fields(text: str) -> dict[str, str]:
    if not text.strip():
        return {}
    loaded: object = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError("fields_json must be a JSON object of text")
    pairs = cast(dict[object, object], loaded)
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in pairs.items()):
        raise ValueError("fields_json must be a JSON object of text")
    return cast(dict[str, str], pairs)


def _generated_key(item_id: str) -> str:
    return "csv-" + hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16]


def _item(cells: dict[str, str]) -> TraceItem:
    fields = _fields(cells["fields_json"])
    if cells.get(NOTICE_COLUMN, "").strip():
        fields = {**fields, NOTICE_FIELD: cells[NOTICE_COLUMN]}
    return TraceItem(
        item_id=cells["item_id"],
        item_key=cells["item_key"] or _generated_key(cells["item_id"]),
        kind=TraceKind(cells["kind"].strip()),
        title=cells["title"],
        fields=fields,
        source_span_refs=_span_refs(cells["source_span_refs"]),
    )


def _link(cells: dict[str, str]) -> TraceLink:
    return TraceLink(
        link_id=cells["link_id"],
        from_id=cells["from_id"],
        to_id=cells["to_id"],
        relation=TraceRelation(cells["relation"].strip()),
    )


def _flag(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered not in {"true", "false"}:
        raise ValueError("required must be true or false")
    return lowered == "true"


def _rule(cells: dict[str, str]) -> CriterionRule:
    return CriterionRule(
        rule_id=cells["rule_id"],
        rule_revision=int(cells["rule_revision"].strip() or "1"),
        criterion_id=cells["criterion_id"],
        measure=cells["measure"],
        comparator=Comparator(cells["comparator"].strip()),
        threshold=_decimal_in(cells["threshold"]),
        unit=cells["unit"],
        condition=cells["condition"],
        rounding=Rounding(
            digits=int(cells["rounding_digits"].strip() or "2"),
            mode=cells["rounding_mode"].strip() or "HALF_UP",  # type: ignore[arg-type]
        ),
        required=_flag(cells["required"]),
    )


def _result(cells: dict[str, str]) -> ResultRecord:
    text = cells["value"]
    value: Decimal | None = None
    raw = cells["raw_value"] or None
    if text.strip():
        try:
            value = _decimal_in(text)
        except (InvalidOperation, ValueError):
            raw = raw or text  # not a number: keep what was written so the verdict can say so
    return ResultRecord(
        result_id=cells["result_id"],
        result_revision=int(cells["result_revision"].strip() or "1"),
        criterion_id=cells["criterion_id"],
        condition=cells["condition"],
        value=value,
        raw_value=raw,
        unit=cells["unit"],
        numerator=_int_or_none(cells["numerator"]),
        denominator=_int_or_none(cells["denominator"]),
        observed_at=datetime.fromisoformat(cells["observed_at"].strip()),
        source_span_refs=_span_refs(cells["source_span_refs"]),
    )


_BUILDERS: dict[str, tuple[str, Callable[[dict[str, str]], BaseModel]]] = {
    "ITEM": ("item_id", _item),
    "LINK": ("link_id", _link),
    "RULE": ("rule_id", _rule),
    "RESULT": ("result_id", _result),
}


def _reference_ids(row_type: str, model: BaseModel) -> Iterable[str]:
    if isinstance(model, TraceLink):
        return (model.from_id, model.to_id)
    if isinstance(model, (CriterionRule, ResultRecord)):
        return (model.criterion_id,)
    return ()


def parse_csv(text: str) -> ParsedCsv:
    """Read the file into checked rows. Problems are collected, nothing is raised for bad rows."""
    parsed = ParsedCsv()
    if len(text) > MAX_CSV_CHARS:
        parsed.issues.append(
            Issue("FILE_TOO_LARGE", detail=f"more than {MAX_CSV_CHARS} characters")
        )
        return parsed
    body = text[1:] if text.startswith(BOM) else text
    try:
        table = list(csv.reader(io.StringIO(body, newline="")))
    except csv.Error as exc:
        parsed.issues.append(Issue("CSV_UNREADABLE", detail=str(exc)[:200]))
        return parsed
    header_at = next((n for n, line in enumerate(table) if any(c.strip() for c in line)), None)
    if header_at is None:
        parsed.issues.append(Issue("CSV_EMPTY", detail="the file has no header row"))
        return parsed
    names = [name.strip() for name in table[header_at]]
    if len(set(names)) != len(names):
        parsed.issues.append(Issue("HEADER_DUPLICATE_COLUMN", row=header_at + 1))
        return parsed
    missing = [name for name in COLUMNS if name not in names]
    if missing:
        parsed.issues.append(
            Issue("HEADER_MISSING_COLUMNS", row=header_at + 1, detail=", ".join(missing))
        )
        return parsed
    parsed.ignored_columns = tuple(
        name for name in names if name not in {*COLUMNS, NOTICE_COLUMN, CLOSURE_COLUMN}
    )
    digests: set[str] = set()
    seen: dict[tuple[str, str], int] = {}
    count = 0
    for number, line in enumerate(table[header_at + 1 :], start=header_at + 2):
        if not any(cell.strip() for cell in line):
            continue
        count += 1
        if count > MAX_ROWS:
            parsed.issues.append(
                Issue("TOO_MANY_ROWS", row=number, detail=f"more than {MAX_ROWS} rows")
            )
            break
        padded = [*line, *([""] * (len(names) - len(line)))]
        cells = {name: unescape_cell(padded[at]) for at, name in enumerate(names)}
        if cells["format"].strip() != FORMAT:
            parsed.issues.append(
                Issue("FORMAT_UNSUPPORTED", number, detail=cells["format"].strip()[:40])
            )
            continue
        if cells["base_set_digest"].strip():
            digests.add(cells["base_set_digest"].strip())
        row_type = cells["row_type"].strip()
        if row_type == "DELETE":
            row = _delete_row(parsed, number, cells)
        elif row_type in _BUILDERS:
            row = _upsert_row(parsed, number, row_type, cells)
        else:
            parsed.issues.append(Issue("ROW_TYPE_UNKNOWN", number, detail=row_type[:40]))
            continue
        if row is None:
            continue
        marker = (
            row.row_type if row.row_type != "DELETE" else "DELETE:" + str(row.target_kind),
            row.key,
        )
        if marker in seen:
            parsed.issues.append(
                Issue("DUPLICATE_ID_IN_FILE", number, row.key, f"also on line {seen[marker]}")
            )
            continue
        seen[marker] = number
        parsed.rows.append(row)
    parsed.base_digests = tuple(sorted(digests))
    return parsed


def _delete_row(parsed: ParsedCsv, number: int, cells: dict[str, str]) -> ParsedRow | None:
    kind, target = cells["target_kind"].strip(), cells["target_id"]
    if kind not in DELETE_KINDS or not target:
        parsed.issues.append(
            Issue(
                "DELETE_ROW_INCOMPLETE",
                number,
                target or None,
                "target_kind and target_id are required",
            )
        )
        return None
    return ParsedRow(row=number, row_type="DELETE", key=target, target_kind=kind)


def _upsert_row(
    parsed: ParsedCsv, number: int, row_type: str, cells: dict[str, str]
) -> ParsedRow | None:
    key_column, build = _BUILDERS[row_type]
    key = cells[key_column]
    if not key:
        parsed.issues.append(Issue("ID_MISSING", number, None, f"{key_column} is empty"))
        return None
    model = _guard(parsed.issues, number, key, lambda: build(cells))
    if model is None:
        return None
    parsed.reference_ids.update(_reference_ids(row_type, model))
    return ParsedRow(
        row=number,
        row_type=row_type,
        key=key,
        model=model,
        key_given=row_type != "ITEM" or bool(cells["item_key"]),
    )


# --- spreadsheet damage to ids -------------------------------------------------------------------

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_DATE_LIKE = re.compile(r"^\d{1,4}([-/.])\d{1,4}(\1\d{1,4})?$")
_EXCEL_DATE = re.compile(
    r"^(\d{1,2}-[A-Za-z]{3}|[A-Za-z]{3}-\d{2}|\d{4}-\d{2}-\d{2}( 00:00:00)?"
    r"|\d{1,2}/\d{1,2}(/\d{2,4})?)$"
)
_EXPONENT = re.compile(r"^\d(\.\d+)?[eE]\+?\d+$")


def _date_tokens(text: str) -> frozenset[int]:
    tokens: set[int] = set()
    for part in re.split(r"[-/ .:]", text):
        if part.isdigit():
            number = int(part)
            if number:
                tokens.add(number % 100 if number >= 1000 else number)
        elif part[:3].lower() in _MONTHS:
            tokens.add(_MONTHS[part[:3].lower()])
    return frozenset(tokens)


def _exponent_of(original: str, incoming: str) -> bool:
    mantissa = incoming.lower().split("e")[0].replace(".", "")
    with localcontext() as context:
        context.prec = max(len(mantissa), 1)
        try:
            return +Decimal(original) == Decimal(incoming)
        except InvalidOperation:
            return False


def spreadsheet_damage(original: str, incoming: str) -> str | None:
    """Why `incoming` looks like a spreadsheet's rewrite of `original`, or None."""
    zeros_lost = (
        original.isdigit()
        and incoming.isdigit()
        and original.startswith("0")
        and original != incoming
        and (original.lstrip("0") or "0") == incoming
    )
    if zeros_lost:
        return "LEADING_ZEROS_LOST"
    if (
        original.isdigit()
        and len(original) >= 12
        and _EXPONENT.match(incoming)
        and _exponent_of(original, incoming)
    ):
        return "EXPONENT_FORM"
    if (
        _DATE_LIKE.match(original)
        and _EXCEL_DATE.match(incoming)
        and _date_tokens(original) <= _date_tokens(incoming)
    ):
        return "DATE_FORM"
    return None


def spreadsheet_suspects(
    existing_ids: Iterable[str], incoming_ids: Iterable[str]
) -> list[tuple[str, str, str]]:
    """Ids in the file that are new but look like a spreadsheet changed an id that is missing."""
    existing, incoming = set(existing_ids), set(incoming_ids)
    lost = sorted(existing - incoming)
    found: list[tuple[str, str, str]] = []
    for name in sorted(incoming - existing):
        for original in lost:
            reason = spreadsheet_damage(original, name)
            if reason is not None:
                found.append((name, original, reason))
                break
    return found
