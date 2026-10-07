"""Synthetic cases for the trace reason tags: inputs, the answer key and the computed verdict view.

Every value is invented (nothing measured). `cases.json` is written from this module and a test
recomputes the verdict view from each case's trace set, so the stored view cannot drift from the
server rule. The web tests read the same file.
Rewrite it with `python -m tests.fixtures.hypothesis_eval.case_builder` (PYTHONPATH=src;.).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from thoth.application.services.verification_trace import (
    compute_verdicts,
    dependency_changes,
    mark_stale,
)
from thoth.domain.verification_trace import (
    Comparator,
    CriterionRule,
    ResultRecord,
    SelectionPolicy,
    TraceItem,
    TraceKind,
    TraceLink,
    TraceRelation,
    TraceSet,
    VerdictRevision,
)

NOTICE = "SYNTHETIC DEMO DATA - NOT MEASURED - NOT APPROVED - NOT FOR ENGINEERING USE"
FILE = Path(__file__).with_name("cases.json")
POLICY = SelectionPolicy()
T0 = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
OBSERVED = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
BANNED = [
    "재시험 필수",
    "원인은",
    "기준을 낮추",
    "통과 가능",
    "승인됨",
    "종결",
    "해결",
    "면제",
    "AI 추천",
]
REQ = "SYN-REQ-1"


def _item(item_id: str, kind: TraceKind, title: str = "") -> TraceItem:
    return TraceItem(
        item_id=item_id,
        item_key="k-" + item_id,
        kind=kind,
        title=title,
        fields={"synthetic_notice": NOTICE},
    )


def _crit(
    number: str,
    *,
    condition: str = "weather=rain",
    rule: bool = True,
    unit: str = "ratio",
    tested: bool = True,
) -> dict[str, Any]:
    return {
        "id": "SYN-C-" + number,
        "condition": condition,
        "rule": rule,
        "unit": unit,
        "tested": tested,
    }


def _result(
    criterion: str,
    value: str | None,
    *,
    condition: str = "weather=rain",
    unit: str = "ratio",
    numerator: int | None = None,
    denominator: int | None = None,
    raw: str | None = None,
    revision: int = 1,
) -> ResultRecord:
    return ResultRecord(
        result_id="SYN-RES-" + criterion,
        result_revision=revision,
        criterion_id="SYN-C-" + criterion,
        condition=condition,
        value=None if value is None else Decimal(value),
        raw_value=raw,
        unit=unit,
        numerator=numerator,
        denominator=denominator,
        observed_at=OBSERVED,
        source_span_refs=("SYN-SOURCE.yaml#" + criterion,),
    )


def _trace(criteria: list[dict[str, Any]], results: list[ResultRecord]) -> TraceSet:
    items = [_item(REQ, TraceKind.REQUIREMENT, "Synthetic requirement (invented example)")]
    links: list[TraceLink] = []
    rules: list[CriterionRule] = []
    for index, spec in enumerate(criteria):
        cid = spec["id"]
        items.append(_item(cid, TraceKind.CRITERION, "Criterion " + cid))
        links.append(
            TraceLink(
                link_id="L-ref-" + cid, from_id=cid, to_id=REQ, relation=TraceRelation.REFINES
            )
        )
        if spec["rule"]:
            rules.append(
                CriterionRule(
                    rule_id="R-" + cid,
                    criterion_id=cid,
                    measure="detection_rate",
                    comparator=Comparator.AT_LEAST,
                    threshold=Decimal("0.90"),
                    unit=spec["unit"],
                    condition=spec["condition"],
                )
            )
        if spec["tested"]:
            case = f"SYN-TC-{index}"
            items.append(_item(case, TraceKind.TEST_CASE, "Test " + case))
            links.append(
                TraceLink(
                    link_id="L-ver-" + cid,
                    from_id=cid,
                    to_id=case,
                    relation=TraceRelation.VERIFIED_BY,
                )
            )
    for result in results:
        items.append(_item(result.result_id, TraceKind.RESULT))
    return TraceSet(
        items=tuple(items), links=tuple(links), rules=tuple(rules), results=tuple(results)
    )


def _check(
    kind: str,
    subject: str,
    state: str,
    heads: list[str],
    tags: list[str],
    *,
    next_step: bool,
    stale: bool = False,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "subject_id": subject,
        "state": state,
        "reason_codes": heads,
        "tags": tags,
        "next_step": next_step,
        "stale": stale,
    }


def _crit_check(
    number: str, state: str, heads: list[str], tags: list[str], **rest: Any
) -> dict[str, Any]:
    return _check("CRITERION", "SYN-C-" + number, state, heads, tags, **rest)


def _req_check(state: str, heads: list[str], tags: list[str], **rest: Any) -> dict[str, Any]:
    return _check("REQUIREMENT", REQ, state, heads, tags, **rest)


def _case(
    case_id: str,
    title: str,
    source: str,
    mode: str,
    before: TraceSet,
    after: TraceSet | None,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": case_id,
        "title": title,
        "origin": source,
        "mode": mode,
        "synthetic_notice": NOTICE,
        "trace_set": before.model_dump(mode="json"),
        "after_trace_set": None if after is None else after.model_dump(mode="json"),
        "checks": checks,
        "banned_phrases": BANNED,
    }


def definitions() -> list[dict[str, Any]]:
    dry = _crit("DET-DRY", condition="weather=dry")
    rain = _crit("DET-RAIN")
    fog = _crit("DET-FOG", condition="weather=fog")
    dry_ok = _result("DET-DRY", "0.95", condition="weather=dry", numerator=19, denominator=20)
    rain_miss = _result("DET-RAIN", "0.80", numerator=16, denominator=20)
    fog_miss = _result("DET-FOG", "0.70", condition="weather=fog", numerator=14, denominator=20)
    miss = "THRESHOLD_NOT_MET"
    edited = _result("DET-RAIN", "0.92", numerator=23, denominator=25, revision=2)
    rain_set = _trace([rain], [rain_miss])
    return [
        _case(
            "fog-no-result",
            "A tested condition has no result yet",
            "demo: fog row, phase 1",
            "plain",
            _trace([dry, fog], [dry_ok]),
            None,
            [
                _crit_check(
                    "DET-FOG", "HOLD_NO_RESULT", ["NO_RESULT"], ["UNTESTED"], next_step=True
                ),
                _crit_check("DET-DRY", "PASS_COMPUTED", [], [], next_step=False),
                _req_check(
                    "HOLD_INCOMPLETE", ["SYN-C-DET-FOG"], ["REQUIRED_INCOMPLETE"], next_step=True
                ),
            ],
        ),
        _case(
            "rain-valid-miss",
            "A usable result misses the threshold",
            "demo: rain row, phase 2",
            "plain",
            rain_set,
            None,
            [
                _crit_check("DET-RAIN", "FAIL_COMPUTED", [miss], ["VALID_MISS"], next_step=True),
                _req_check("FAIL", ["SYN-C-DET-RAIN"], [], next_step=True),
            ],
        ),
        _case(
            "rain-miss-and-fog-untested",
            "A miss and an untested condition at once",
            "demo: requirement row, phase 2",
            "plain",
            _trace([rain, fog], [rain_miss]),
            None,
            [
                _crit_check("DET-RAIN", "FAIL_COMPUTED", [miss], ["VALID_MISS"], next_step=True),
                _crit_check(
                    "DET-FOG", "HOLD_NO_RESULT", ["NO_RESULT"], ["UNTESTED"], next_step=True
                ),
                _req_check(
                    "FAIL_WITH_INCOMPLETE_COVERAGE",
                    ["SYN-C-DET-RAIN", "SYN-C-DET-FOG"],
                    ["REQUIRED_INCOMPLETE"],
                    next_step=True,
                ),
            ],
        ),
        _case(
            "dry-pass",
            "A met line gets no tags and no next check",
            "demo: dry row",
            "plain",
            _trace([dry], [dry_ok]),
            None,
            [
                _crit_check("DET-DRY", "PASS_COMPUTED", [], [], next_step=False),
                _req_check("PASS", [], [], next_step=False),
            ],
        ),
        _case(
            "rain-result-edited",
            "A result is edited and the verdict is not yet recomputed",
            "demo: rain row, edited",
            "stale",
            rain_set,
            _trace([rain], [edited]),
            [
                _crit_check(
                    "DET-RAIN",
                    "FAIL_COMPUTED",
                    [miss],
                    ["VALID_MISS", "BASIS_CHANGED"],
                    next_step=True,
                    stale=True,
                ),
                _req_check(
                    "FAIL", ["SYN-C-DET-RAIN"], ["BASIS_CHANGED"], next_step=True, stale=True
                ),
            ],
        ),
        _case(
            "rain-result-edited-recomputed",
            "The edited result is recomputed and now meets the threshold",
            "demo: rain row, edited and recomputed",
            "recomputed",
            rain_set,
            _trace([rain], [edited]),
            [
                _crit_check("DET-RAIN", "PASS_COMPUTED", [], [], next_step=False),
                _req_check("PASS", [], [], next_step=False),
            ],
        ),
        _case(
            "unit-mismatch",
            "The result unit differs from the rule unit",
            "new",
            "plain",
            _trace([rain], [_result("DET-RAIN", "80", unit="percent")]),
            None,
            [
                _crit_check(
                    "DET-RAIN", "HOLD_INVALID_RESULT", ["UNIT_MISMATCH"], ["UNIT"], next_step=True
                ),
                _req_check(
                    "HOLD_INCOMPLETE", ["SYN-C-DET-RAIN"], ["REQUIRED_INCOMPLETE"], next_step=True
                ),
            ],
        ),
        _case(
            "denominator-zero",
            "The denominator is zero",
            "new",
            "plain",
            _trace([rain], [_result("DET-RAIN", "0.50", numerator=5, denominator=0)]),
            None,
            [
                _crit_check(
                    "DET-RAIN",
                    "HOLD_INVALID_RESULT",
                    ["DENOMINATOR_NOT_POSITIVE"],
                    ["DENOMINATOR"],
                    next_step=True,
                )
            ],
        ),
        _case(
            "value-disagrees-with-counts",
            "The value is not numerator over denominator",
            "new",
            "plain",
            _trace([rain], [_result("DET-RAIN", "0.50", numerator=9, denominator=10)]),
            None,
            [
                _crit_check(
                    "DET-RAIN",
                    "HOLD_INVALID_RESULT",
                    ["VALUE_DISAGREES_WITH_COUNTS"],
                    ["DENOMINATOR"],
                    next_step=True,
                )
            ],
        ),
        _case(
            "value-not-a-number",
            "The value was written as text",
            "new",
            "plain",
            _trace([rain], [_result("DET-RAIN", None, raw="n/a")]),
            None,
            [
                _crit_check(
                    "DET-RAIN",
                    "HOLD_INVALID_RESULT",
                    ["VALUE_NOT_A_NUMBER"],
                    ["VALUE"],
                    next_step=True,
                )
            ],
        ),
        _case(
            "condition-mismatch",
            "A result exists but for another condition",
            "new",
            "plain",
            _trace([rain], [_result("DET-RAIN", "0.95", condition="weather=fog")]),
            None,
            [
                _crit_check(
                    "DET-RAIN",
                    "HOLD_NO_RESULT",
                    ["NO_RESULT"],
                    ["CONDITION_MISMATCH"],
                    next_step=True,
                ),
                _req_check(
                    "HOLD_INCOMPLETE", ["SYN-C-DET-RAIN"], ["REQUIRED_INCOMPLETE"], next_step=True
                ),
            ],
        ),
        _case(
            "no-rule",
            "A criterion has no rule",
            "new",
            "plain",
            _trace([_crit("DET-RAIN", rule=False)], []),
            None,
            [
                _crit_check("DET-RAIN", "HOLD_NO_RULE", ["NO_RULE"], ["NO_RULE"], next_step=True),
                _req_check(
                    "HOLD_INCOMPLETE", ["SYN-C-DET-RAIN"], ["REQUIRED_INCOMPLETE"], next_step=True
                ),
            ],
        ),
        _case(
            "no-test-linked",
            "A criterion has a rule but no test is linked",
            "new",
            "plain",
            _trace([_crit("DET-RAIN", tested=False)], []),
            None,
            [
                _crit_check(
                    "DET-RAIN", "HOLD_NO_RESULT", ["NO_RESULT"], ["UNLINKED"], next_step=True
                )
            ],
        ),
        _case(
            "two-conditions-miss",
            "Two conditions miss at the same time",
            "new",
            "plain",
            _trace([dry, rain, fog], [dry_ok, rain_miss, fog_miss]),
            None,
            [
                _crit_check("DET-DRY", "PASS_COMPUTED", [], [], next_step=False),
                _crit_check("DET-RAIN", "FAIL_COMPUTED", [miss], ["VALID_MISS"], next_step=True),
                _crit_check("DET-FOG", "FAIL_COMPUTED", [miss], ["VALID_MISS"], next_step=True),
                _req_check("FAIL", ["SYN-C-DET-RAIN", "SYN-C-DET-FOG"], [], next_step=True),
            ],
        ),
    ]


def _view_of(
    current: TraceSet, verdicts: tuple[VerdictRevision, ...], stale: dict[Any, Any], pending: Any
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for verdict in sorted(verdicts, key=lambda item: (item.subject_kind.value, item.subject_id)):
        shown = verdict.model_dump(mode="json")
        entries.append(
            {
                **shown,
                "currentness": stale[(verdict.subject_kind, verdict.subject_id)].model_dump(
                    mode="json"
                ),
                "confirmations": [],
                "recent_history": [],
                "history_total": 1,
            }
        )
    return {
        "record_digest": None,
        "set_digest": current.set_digest,
        "items": [item.model_dump(mode="json") for item in current.items],
        "links": [item.model_dump(mode="json") for item in current.links],
        "rules": [item.model_dump(mode="json") for item in current.rules],
        "results": [item.model_dump(mode="json") for item in current.results],
        "policy": POLICY.model_dump(mode="json"),
        "verdicts": entries,
        "confirmations": [],
        "pending_changes": [item.model_dump(mode="json") for item in pending],
    }


def view_of_case(case: dict[str, Any]) -> dict[str, Any]:
    """The trace view the server would show for this case (what trace/read returns)."""
    before = TraceSet.model_validate(case["trace_set"])
    verdicts = compute_verdicts(before, POLICY, computed_at=T0, trigger="INITIAL_COMPUTE")
    if case["mode"] == "plain":
        return _view_of(before, verdicts, mark_stale(before, ()), ())
    after = TraceSet.model_validate(case["after_trace_set"])
    changes = dependency_changes(before, after)
    if case["mode"] == "stale":
        return _view_of(after, verdicts, mark_stale(after, changes), changes)
    again = compute_verdicts(
        after, POLICY, computed_at=T1, trigger="RECOMPUTE", changes=changes, previous=verdicts
    )
    return _view_of(after, again, mark_stale(after, ()), ())


def build() -> dict[str, Any]:
    cases = definitions()
    for case in cases:
        case["view"] = view_of_case(case)
    return {"schema": "hypothesis-eval-cases/1", "synthetic_notice": NOTICE, "cases": cases}


def main() -> None:
    FILE.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {FILE.name}: {len(build()['cases'])} cases")


if __name__ == "__main__":
    main()
