"""Confirmed findings are adopted only when every cited span was supplied; the rest are counted."""

from __future__ import annotations

from typing import cast

from thoth.adapters.models.codex_oauth import strict_output_schema
from thoth.adapters.models.reference_schema import constrain_span_references
from thoth.application.services.research_findings import (
    MAX_CONFIRMED_FINDINGS,
    adopt_confirmed_findings,
)
from thoth.domain.evidence_requirements import ConfirmedFinding, ReviewProposal


def finding(
    refs: tuple[str, ...] = ("span:a",),
    statement: str = "문서에 적힌 사실",
    kind: str = "DOCUMENT_FACT",
) -> ConfirmedFinding:
    return ConfirmedFinding.model_validate(
        {"statement": statement, "evidence_refs": refs, "requirement_id": None, "kind": kind}
    )


def test_an_older_review_payload_without_the_field_still_reads() -> None:
    proposal = ReviewProposal.model_validate({"candidates": [], "answer": "old"})
    assert proposal.confirmed_findings == ()


def test_findings_citing_only_supplied_spans_are_adopted_in_order() -> None:
    adopted, dropped = adopt_confirmed_findings(
        (finding(), finding(("span:a", "span:b"), "찾아봤지만 없음", "VALID_NEGATIVE_FINDING")),
        {"span:a", "span:b"},
    )
    assert [item["statement"] for item in adopted] == ["문서에 적힌 사실", "찾아봤지만 없음"]
    assert adopted[1]["kind"] == "VALID_NEGATIVE_FINDING"
    assert adopted[1]["evidence_refs"] == ["span:a", "span:b"]
    assert dropped == 0


def test_a_span_that_was_not_supplied_drops_the_whole_finding_and_is_counted() -> None:
    adopted, dropped = adopt_confirmed_findings(
        (finding(("span:a", "span:ghost")), finding(("span:ghost",)), finding(())), {"span:a"}
    )
    assert adopted == []
    assert dropped == 3


def test_blank_statements_and_findings_beyond_the_cap_are_dropped() -> None:
    many = tuple(finding(statement=f"사실 {i}") for i in range(MAX_CONFIRMED_FINDINGS + 3))
    adopted, dropped = adopt_confirmed_findings((finding(statement="   "), *many), {"span:a"})
    assert len(adopted) == MAX_CONFIRMED_FINDINGS
    assert dropped == 1 + 3


def test_the_provider_schema_limits_finding_citations_to_supplied_spans() -> None:
    schema = constrain_span_references(strict_output_schema(ReviewProposal), ("span:a", "span:b"))
    defs = cast(dict[str, dict[str, object]], schema["$defs"])
    properties = cast(dict[str, dict[str, object]], defs["ConfirmedFinding"]["properties"])
    assert properties["evidence_refs"]["items"] == {"type": "string", "enum": ["span:a", "span:b"]}
    description = str(cast(dict[str, object], schema["properties"])["confirmed_findings"])
    assert "성능 충족" in description
