"""The synthetic radar demo as a trace set, shared by the trace tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

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
)

DEMO = Path(__file__).parents[2] / "examples" / "synthetic-radar-demo-v1"
SCORER = (
    Path(__file__).parents[2]
    / "examples"
    / "synthetic-radar-demo-v1-scorer"
    / "40_ASSESSMENT_DRAFT_SYNTHETIC.yaml"
)
T0 = datetime(2026, 10, 2, tzinfo=UTC)
POLICY = SelectionPolicy()
REQ = "SYN-REQ-RAD-001"
NOTICE = "SYNTHETIC DEMO DATA - NOT MEASURED - NOT APPROVED - NOT FOR ENGINEERING USE"


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def with_notice(trace_set: TraceSet) -> TraceSet:
    """The same set with the synthetic notice on every item, as the demo script sets it."""
    items = tuple(
        item.model_copy(update={"fields": {**item.fields, "synthetic_notice": NOTICE}})
        for item in trace_set.items
    )
    return TraceSet(
        items=items, links=trace_set.links, rules=trace_set.rules, results=trace_set.results
    )


def demo_set(phase: int) -> TraceSet:
    """The synthetic radar demo as a trace set; phase 1 has the dry result, phase 2 adds rain."""
    requirement = read_yaml(DEMO / "10_REQUIREMENTS_SYNTHETIC.yaml")["requirement"]
    cases = read_yaml(DEMO / "20_TEST_PLAN_SYNTHETIC.yaml")["test_cases"]
    items = [
        TraceItem(
            item_id=requirement["id"],
            item_key="k-req",
            kind=TraceKind.REQUIREMENT,
            title=requirement["title"],
        )
    ]
    links: list[TraceLink] = []
    rules: list[CriterionRule] = []
    for number, criterion in enumerate(requirement["criteria"]):
        cid = criterion["id"]
        items.append(
            TraceItem(
                item_id=cid,
                item_key=f"k-c{number}",
                kind=TraceKind.CRITERION,
                title=criterion["title"],
            )
        )
        links.append(
            TraceLink(
                link_id=f"L-ref-{cid}", from_id=cid, to_id=REQ, relation=TraceRelation.REFINES
            )
        )
        rules.append(
            CriterionRule(
                rule_id=f"R-{cid}",
                criterion_id=cid,
                measure=criterion["measure"],
                comparator=Comparator(criterion["comparator"]),
                threshold=Decimal(criterion["threshold"]),
                unit=criterion["unit"],
                condition=criterion["condition"],
                required=criterion["required"],
            )
        )
    for number, case in enumerate(cases):
        items.append(
            TraceItem(
                item_id=case["id"],
                item_key=f"k-t{number}",
                kind=TraceKind.TEST_CASE,
                title=case["title"],
            )
        )
        links.extend(
            TraceLink(
                link_id=f"L-ver-{cid}",
                from_id=cid,
                to_id=case["id"],
                relation=TraceRelation.VERIFIED_BY,
            )
            for cid in case["covers"]
        )
    results: list[ResultRecord] = []
    for path in sorted(DEMO.glob("3*_RESULT_*.yaml")):
        data = read_yaml(path)
        if data["available_from_phase"] > phase:
            continue
        for key in ("detection", "false_track"):
            part = data[key]
            rid = f"SYN-RES-{part['criterion']}"
            items.append(TraceItem(item_id=rid, item_key=f"k-{rid}", kind=TraceKind.RESULT))
            links.append(
                TraceLink(
                    link_id=f"L-prod-{rid}",
                    from_id=data["test_case"],
                    to_id=rid,
                    relation=TraceRelation.PRODUCES,
                )
            )
            results.append(
                ResultRecord(
                    result_id=rid,
                    criterion_id=part["criterion"],
                    condition=data["condition"],
                    value=Decimal(part["value"]),
                    unit=part["unit"],
                    numerator=part["numerator"],
                    denominator=part["denominator"],
                    observed_at=datetime.fromisoformat(data["observed_at"]),
                    source_span_refs=(f"{path.name}#{key}",),
                )
            )
    return TraceSet(
        items=tuple(items), links=tuple(links), rules=tuple(rules), results=tuple(results)
    )
