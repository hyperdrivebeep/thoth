"""What a CSV import would change, worked out before anything is saved, and the apply step.

Preview and apply run the same planning on the same input, so what the user saw is what is applied.
The preview id is bound to the exact text that was sent and to the set the trace has right now:
if either differs at apply time, nothing is applied. An apply is one write that stores the new set
and the verdicts it changes together.

Rows in the file add or change things (matched by id). Rows that are missing from the file change
nothing. Only a DELETE row removes something, and an item that is still linked is never removed
along with its links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel

from thoth.application.services.trace_csv import (
    LOSS_MANIFEST,
    NOTICE_FIELD,
    Issue,
    ParsedRow,
    input_sha256,
    parse_csv,
    spreadsheet_suspects,
)
from thoth.application.services.verification_trace import (
    TraceError,
    VerificationTraceService,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.verification_trace import (
    CriterionRule,
    ResultRecord,
    TraceItem,
    TraceLink,
    TraceSet,
    VerificationTraceRecord,
)

ImportMode = Literal["CREATE", "UPDATE"]
_STOPPING = {"STALE_BASE", "BASE_DIGEST_MISSING", "BASE_DIGEST_INCONSISTENT"}


class ImportRejected(TraceError):
    pass


@dataclass(frozen=True)
class PlannedChange:
    kind: str
    id: str
    row: int
    fields: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "row": self.row, "fields": list(self.fields)}


@dataclass
class ImportPlan:
    mode: str
    input_sha256: str
    preview_id: str
    current_set_digest: str | None
    added: list[PlannedChange] = field(default_factory=lambda: list[PlannedChange]())
    updated: list[PlannedChange] = field(default_factory=lambda: list[PlannedChange]())
    deleted: list[PlannedChange] = field(default_factory=lambda: list[PlannedChange]())
    unchanged: int = 0
    conflicts: list[Issue] = field(default_factory=lambda: list[Issue]())
    losses: list[str] = field(default_factory=lambda: list(LOSS_MANIFEST))
    new_set: TraceSet | None = None

    @property
    def applicable(self) -> bool:
        return not self.conflicts

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.updated or self.deleted)

    def as_json(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "input_sha256": self.input_sha256,
            "mode": self.mode,
            "current_set_digest": self.current_set_digest,
            "applicable": self.applicable,
            "changes": {
                "added": [item.as_json() for item in self.added],
                "updated": [item.as_json() for item in self.updated],
                "deleted": [item.as_json() for item in self.deleted],
                "unchanged": self.unchanged,
            },
            "conflicts": [item.as_json() for item in self.conflicts],
            "losses": self.losses,
            "spreadsheet_suspects": [
                item.as_json() for item in self.conflicts if item.code == "EXCEL_ID_SUSPECTED"
            ],
        }


def _preview_id(project_id: str, mode: str, sha: str, set_digest: str | None) -> str:
    payload: dict[str, object] = {
        "project_id": project_id,
        "mode": mode,
        "input_sha256": sha,
        "current_set_digest": set_digest,
    }
    return domain_digest("TRACE_IMPORT_PREVIEW", "1.0.0", canonical_payload(payload))


def _changed_fields(old: BaseModel, new: BaseModel) -> tuple[str, ...]:
    before, after = old.model_dump(mode="python"), new.model_dump(mode="python")
    return tuple(sorted(name for name in before if before[name] != after[name]))


def _empty(trace_set: TraceSet) -> bool:
    return not (trace_set.items or trace_set.links or trace_set.rules or trace_set.results)


def plan_import(
    project_id: str, current: VerificationTraceRecord | None, mode: ImportMode, text: str
) -> ImportPlan:
    parsed = parse_csv(text)
    sha = input_sha256(text)
    now = None if current is None else current.trace_set.set_digest
    plan = ImportPlan(
        mode=mode,
        input_sha256=sha,
        preview_id=_preview_id(project_id, mode, sha, now),
        current_set_digest=now,
        conflicts=list(parsed.issues),
        losses=[*LOSS_MANIFEST, *(f"column ignored: {name}" for name in parsed.ignored_columns)],
    )
    if parsed.issues and not parsed.rows and not parsed.base_digests:
        return plan  # the file could not be read as this format at all
    existing = TraceSet() if current is None else current.trace_set
    if mode == "CREATE":
        if not _empty(existing):
            plan.conflicts.append(
                Issue("MODE_CREATE_NEEDS_EMPTY_PROJECT", detail="this project already has a trace")
            )
        plan.conflicts.extend(
            Issue("DELETE_NOT_ALLOWED_IN_CREATE", row.row, row.key)
            for row in parsed.rows
            if row.row_type == "DELETE"
        )
        if not parsed.rows and not parsed.issues:
            plan.conflicts.append(Issue("CSV_HAS_NO_ROWS"))
    else:
        if current is None:
            plan.conflicts.append(
                Issue("UPDATE_NEEDS_EXISTING_TRACE", detail="use CREATE for an empty project")
            )
            return plan
        stop = _base_conflict(parsed.base_digests, existing.set_digest)
        if stop is not None:
            plan.conflicts.append(stop)
            return plan
    _apply_rows(plan, existing, parsed.rows, parsed.reference_ids, mode)
    return plan


def _base_conflict(digests: tuple[str, ...], now: str) -> Issue | None:
    if not digests:
        return Issue("BASE_DIGEST_MISSING", detail="base_set_digest is empty in every row")
    if len(digests) > 1:
        return Issue(
            "BASE_DIGEST_INCONSISTENT", detail="rows name different base_set_digest values"
        )
    named = next(iter(digests))
    if named != now:
        return Issue(
            "STALE_BASE",
            detail=f"the file was made from set {named[:12]}, the trace is now {now[:12]}",
        )
    return None


_STORES = {"ITEM": "items", "LINK": "links", "RULE": "rules", "RESULT": "results"}
_KEYS = {"ITEM": "item_id", "LINK": "link_id", "RULE": "rule_id", "RESULT": "result_id"}


def _apply_rows(
    plan: ImportPlan,
    existing: TraceSet,
    rows: list[ParsedRow],
    reference_ids: set[str],
    mode: str,
) -> None:
    final: dict[str, dict[str, BaseModel]] = {
        kind: {getattr(model, _KEYS[kind]): model for model in getattr(existing, name)}
        for kind, name in _STORES.items()
    }
    upserted: set[tuple[str, str]] = set()
    for row in rows:
        if row.row_type == "DELETE" or row.model is None:
            continue
        upserted.add((row.row_type, row.key))
        _upsert(plan, final[row.row_type], row)
    deleted_items: set[str] = set()
    for row in rows:
        if row.row_type != "DELETE" or mode == "CREATE":
            continue
        kind = str(row.target_kind)
        if (kind, row.key) in upserted:
            plan.conflicts.append(Issue("DELETE_AND_UPSERT_SAME_TARGET", row.row, row.key))
        elif row.key not in final[kind]:
            plan.conflicts.append(Issue("DELETE_TARGET_NOT_FOUND", row.row, row.key, kind))
        else:
            del final[kind][row.key]
            plan.deleted.append(PlannedChange(kind, row.key, row.row))
            if kind == "ITEM":
                deleted_items.add(row.key)
    _keep_links(plan, final, deleted_items)
    _spreadsheet_check(plan, existing, rows, reference_ids, mode)
    if plan.conflicts:
        return
    try:
        plan.new_set = TraceSet(
            items=tuple(cast(dict[str, TraceItem], final["ITEM"]).values()),
            links=tuple(cast(dict[str, TraceLink], final["LINK"]).values()),
            rules=tuple(cast(dict[str, CriterionRule], final["RULE"]).values()),
            results=tuple(cast(dict[str, ResultRecord], final["RESULT"]).values()),
        )
    except ValueError as exc:
        found = re.findall(r"TRACE_[A-Z_]+(?::[^\s\]]+)?", str(exc))
        plan.conflicts.append(
            Issue("GRAPH_INVALID", detail=(found[0] if found else str(exc))[:200])
        )


def _upsert(plan: ImportPlan, store: dict[str, BaseModel], row: ParsedRow) -> None:
    model = row.model
    assert model is not None
    old = store.get(row.key)
    if isinstance(model, TraceItem) and isinstance(old, TraceItem):
        if not row.key_given:
            model = model.model_copy(update={"item_key": old.item_key})
        elif model.item_key != old.item_key:
            plan.conflicts.append(
                Issue("ITEM_KEY_CHANGED", row.row, row.key, "the internal key cannot be changed")
            )
            return
        if NOTICE_FIELD in old.fields and NOTICE_FIELD not in model.fields:
            # A row that says nothing about the synthetic notice cannot take the mark away.
            model = model.model_copy(
                update={"fields": {**model.fields, NOTICE_FIELD: old.fields[NOTICE_FIELD]}}
            )
    if old is None:
        plan.added.append(PlannedChange(row.row_type, row.key, row.row))
    elif old == model:
        plan.unchanged += 1
        return
    else:
        plan.updated.append(
            PlannedChange(row.row_type, row.key, row.row, _changed_fields(old, model))
        )
    store[row.key] = model


def _keep_links(
    plan: ImportPlan, final: dict[str, dict[str, BaseModel]], deleted_items: set[str]
) -> None:
    """An item that is deleted must have nothing left that points at it."""
    for link in cast(dict[str, TraceLink], final["LINK"]).values():
        for end in (link.from_id, link.to_id):
            if end in deleted_items:
                plan.conflicts.append(
                    Issue("DELETE_BLOCKED_BY_LINK", None, end, f"still linked by {link.link_id}")
                )
    for rule in cast(dict[str, CriterionRule], final["RULE"]).values():
        if rule.criterion_id in deleted_items:
            plan.conflicts.append(
                Issue(
                    "DELETE_BLOCKED_BY_RULE",
                    None,
                    rule.criterion_id,
                    f"still has rule {rule.rule_id}",
                )
            )
    for result in cast(dict[str, ResultRecord], final["RESULT"]).values():
        for end in (result.criterion_id, result.result_id):
            if end in deleted_items:
                plan.conflicts.append(
                    Issue(
                        "DELETE_BLOCKED_BY_RESULT",
                        None,
                        end,
                        f"still has result {result.result_id}",
                    )
                )


def _spreadsheet_check(
    plan: ImportPlan,
    existing: TraceSet,
    rows: list[ParsedRow],
    reference_ids: set[str],
    mode: str,
) -> None:
    if mode != "UPDATE":
        return
    known = {
        *(item.item_id for item in existing.items),
        *(link.link_id for link in existing.links),
        *(rule.rule_id for rule in existing.rules),
        *(result.result_id for result in existing.results),
    }
    incoming = {row.key for row in rows} | reference_ids
    for name, original, reason in spreadsheet_suspects(known, incoming):
        at = next((row.row for row in rows if row.key == name), None)
        plan.conflicts.append(
            Issue(
                "EXCEL_ID_SUSPECTED",
                at,
                name,
                f"looks like {original} changed by a spreadsheet ({reason})",
            )
        )


class TraceImportService:
    """Preview and apply for the trace CSV, on top of the trace service."""

    def __init__(self, trace: VerificationTraceService) -> None:
        self._trace = trace

    def preview(self, project_id: str, mode: ImportMode, text: str) -> ImportPlan:
        _, current = self._trace.read(project_id)
        return plan_import(project_id, current, mode, text)

    def apply(
        self,
        project_id: str,
        mode: ImportMode,
        text: str,
        preview_id: str,
        sha256: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if input_sha256(text) != sha256:
            raise ImportRejected("TRACE_IMPORT_INPUT_CHANGED")
        digest, current = self._trace.read(project_id)
        plan = plan_import(project_id, current, mode, text)
        if plan.preview_id != preview_id:
            raise ImportRejected("TRACE_IMPORT_PREVIEW_STALE")
        if plan.conflicts:
            codes = ",".join(sorted({item.code for item in plan.conflicts}))
            raise ImportRejected(f"TRACE_IMPORT_NOT_APPLICABLE:{codes}")
        before = {} if current is None else self._trace.current_verdicts(project_id, current)
        if not plan.has_changes or plan.new_set is None:
            return {
                "applied": False,
                "reason": "NO_CHANGES",
                "record_digest": digest,
                "verdict_changes": [],
            }
        written = self._trace.apply_import(project_id, plan.new_set, digest, actor_id)
        _, after_record = self._trace.read(project_id)
        assert after_record is not None
        changes = [
            {
                "subject_kind": key[0].value,
                "subject_id": key[1],
                "before": None if key not in before else before[key].state.value,
                "after": revision.state.value,
            }
            for key, revision in self._trace.current_verdicts(project_id, after_record).items()
            if key not in before or before[key].revision_digest != revision.revision_digest
        ]
        return {
            "applied": True,
            "record_digest": written,
            "set_digest": after_record.trace_set.set_digest,
            "counts": {
                "added": len(plan.added),
                "updated": len(plan.updated),
                "deleted": len(plan.deleted),
            },
            "verdict_changes": changes,
        }
