from typing import cast

from thoth.application.services.research_progress_view import (
    evidence_focus_payload,
    merge_draft_progress,
)
from thoth.domain.evidence import EvidenceSpan


def _span(span_id: str, page: int, text: str) -> EvidenceSpan:
    return EvidenceSpan.model_validate(
        {
            "span_id": span_id,
            "project_id": "project:p",
            "artifact_id": "artifact:a",
            "source_version_id": "version:v",
            "locator": {"page": page},
            "exact_text": text,
            "text_sha256": "a" * 64,
            "extraction_method": "fixture",
            "support_state": "UNRESOLVED",
            "authority_state": "INFORMAL",
            "verification_state": "NOT_CHECKED",
            "cutoff_state": "ELIGIBLE",
        }
    )


def test_focus_payload_caps_and_keeps_page() -> None:
    spans = tuple(_span(f"span:{i}", 6, f"cell-{i}") for i in range(15))
    payload = evidence_focus_payload(spans)
    assert payload["total"] == 15
    assert payload["truncated"] is True
    assert payload["span_ids"] == [f"span:{i}" for i in range(12)]
    locators: object = payload["locators"]
    assert isinstance(locators, list) and locators
    first: object = cast(list[object], locators)[0]
    assert isinstance(first, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], first))
    assert cast(dict[str, object], first)["page"] == 6


def test_later_draft_keeps_evidence_focus() -> None:
    focus = {"span_ids": ["span:1"]}
    merged = merge_draft_progress(
        {"evidence_focus": focus, "state": "OLD"},
        {"state": "DRAFT_NOT_COMMITTED", "portfolio": {}},
    )
    assert merged["evidence_focus"] == focus
    assert merged["state"] == "DRAFT_NOT_COMMITTED"
    assert "portfolio" in merged
