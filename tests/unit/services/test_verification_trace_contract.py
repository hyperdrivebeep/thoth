"""Compatibility of the original trace imports and deterministic calculation output."""

from datetime import timedelta

from tests.integration.trace_demo_helpers import POLICY, T0
from tests.unit.services.test_verification_trace import mini, result

from thoth.application.services.verification_trace import (
    KEY,
    REVISION_PREFIX,
    TraceConflict,
    TraceError,
    compute_verdicts,
    dependency_changes,
    mark_stale,
)
from thoth.application.services.verification_trace_import import ImportRejected
from thoth.domain.verification_trace import CurrentnessState, SubjectKind, TraceSet


def test_original_constants_and_exception_identity_are_preserved() -> None:
    assert KEY == "verification-trace:project"
    assert REVISION_PREFIX == "trace-verdict:"
    assert TraceError.__module__ == "thoth.application.services.verification_trace"
    assert TraceConflict.__module__ == "thoth.application.services.verification_trace"
    assert TraceConflict.__bases__ == (TraceError,)
    assert ImportRejected.__bases__ == (TraceError,)


def test_selection_ties_output_order_and_reused_revision_identity_are_stable() -> None:
    trace = mini(
        [
            result("Z", value="0.10", revision=2),
            result("OLD", value="0.90"),
            result("DRY", value="0.99", condition="weather=dry"),
            result("A", value="0.20", revision=2),
        ]
    )
    first = compute_verdicts(trace, POLICY, computed_at=T0)
    assert [(v.subject_kind, v.subject_id) for v in first] == [
        (SubjectKind.CRITERION, "C-1"),
        (SubjectKind.REQUIREMENT, "REQ-1"),
    ]
    selection = first[0].selection
    assert selection is not None
    assert [item.result_id for item in selection.candidates] == ["A", "DRY", "OLD", "Z"]
    assert [item.result_id for item in selection.chosen] == ["Z"]
    assert [item.result_id for item in selection.excluded] == ["OLD", "A", "DRY"]
    shuffled = TraceSet(
        items=tuple(reversed(trace.items)),
        links=tuple(reversed(trace.links)),
        rules=tuple(reversed(trace.rules)),
        results=tuple(reversed(trace.results)),
    )
    reordered = compute_verdicts(shuffled, POLICY, computed_at=T0)
    assert [v.model_dump_json() for v in reordered] == [v.model_dump_json() for v in first]
    later = compute_verdicts(
        shuffled, POLICY, computed_at=T0 + timedelta(days=10), previous=iter(first)
    )
    assert all(now is before for now, before in zip(later, first, strict=True))


def test_decimal_time_and_dependency_field_order_are_preserved() -> None:
    before = mini([result(value="0.1000")])
    after = mini([result(value="0.2000", day=1, revision=2)])
    verdict = compute_verdicts(before, POLICY, computed_at=T0)[0]
    payload = verdict.model_dump(mode="json")
    assert payload["conditions"]["threshold"] == "0.50"
    assert payload["actor"]["computed_at"] == "2026-10-02T00:00:00Z"
    changes = dependency_changes(before, after)
    assert [(item.kind, item.field, item.before, item.after) for item in changes] == [
        ("RESULT", "observed_at", "2026-10-02 00:00:00+00:00", "2026-10-03 00:00:00+00:00"),
        ("RESULT", "result_revision", "1", "2"),
        ("RESULT", "value", "0.1000", "0.2000"),
    ]
    assert all(item.criterion_ids == ("C-1",) for item in changes)
    assert all(item.requirement_ids == ("REQ-1",) for item in changes)
    stale = mark_stale(after, iter(changes))
    assert list(stale) == [(SubjectKind.CRITERION, "C-1"), (SubjectKind.REQUIREMENT, "REQ-1")]
    assert all(item.state is CurrentnessState.STALE_BASIS for item in stale.values())
