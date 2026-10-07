"""The trace as one JSON value for a screen or a client: set, current verdicts, recent history."""

from __future__ import annotations

from typing import Any

from thoth.application.services.verification_trace import VerificationTraceService, mark_stale
from thoth.domain.verification_trace import (
    SubjectKind,
    TraceSet,
    VerdictRevision,
    VerificationTraceRecord,
    subject_key,
)

RECENT_HISTORY = 3  # revisions of each verdict that come with trace/read; the rest is trace/history


def revision_json(revision: VerdictRevision) -> dict[str, Any]:
    return revision.model_dump(mode="json")


def trace_view(
    service: VerificationTraceService,
    project_id: str,
    record_digest: str | None,
    record: VerificationTraceRecord | None,
) -> dict[str, Any]:
    trace_set = TraceSet() if record is None else record.trace_set
    view: dict[str, Any] = {
        "record_digest": record_digest,
        "set_digest": trace_set.set_digest,
        "items": [item.model_dump(mode="json") for item in trace_set.items],
        "links": [item.model_dump(mode="json") for item in trace_set.links],
        "rules": [item.model_dump(mode="json") for item in trace_set.rules],
        "results": [item.model_dump(mode="json") for item in trace_set.results],
        "policy": None if record is None else record.policy.model_dump(mode="json"),
        "verdicts": [],
        "confirmations": [],
        "pending_changes": [],
    }
    if record is None:
        return view
    stale = mark_stale(record.trace_set, record.pending_changes)
    for (kind, subject_id), digest in sorted(
        record.current_digests().items(), key=lambda pair: (pair[0][0].value, pair[0][1])
    ):
        if (kind, subject_id) not in stale:
            continue  # a subject that is no longer in the set keeps its history, but is not shown
        page = service.history(
            project_id, record, SubjectKind(kind), subject_id, limit=RECENT_HISTORY
        )
        current = page.revisions[0]
        assert current.revision_digest == digest
        view["verdicts"].append(
            {
                **revision_json(current),
                "currentness": stale[(kind, subject_id)].model_dump(mode="json"),
                "confirmations": [
                    item.model_dump(mode="json") for item in record.confirmations_of(digest)
                ],
                "recent_history": [revision_json(item) for item in page.revisions],
                "history_total": len(record.history[subject_key(kind, subject_id)]),
            }
        )
    view["confirmations"] = [item.model_dump(mode="json") for item in record.confirmations]
    view["pending_changes"] = [item.model_dump(mode="json") for item in record.pending_changes]
    return view
