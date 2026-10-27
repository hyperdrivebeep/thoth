"""G3 1c: an evaluation case made from a request is evaluated to what the request itself selects."""

import json
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError
from tests.unit.test_fused_selection import fused
from tests.unit.test_ranked_lanes import policy as lane_policy
from tests.unit.test_ranked_lanes import report

from thoth.adapters.behavior.registry import evaluate_retrieval
from thoth.domain.behavior_policy import (
    RetrievalBehaviorInput,
    RetrievalBehaviorPolicy,
    RetrievalLanePolicy,
    RetrievalPolicy,
)
from thoth.domain.retrieval_case import (
    catalog_context,
    catalog_input,
    evaluation_case_payload,
)
from thoth.domain.retrieval_lane import (
    CHAR_BIGRAM,
    GROUPED_BM25,
    LEXICAL_SUBSTRING_V1,
    PROTECTED_EXACT,
    CatalogContext,
    RetrievalRequest,
    select_retrieval_context,
)

POLICIES: dict[str, RetrievalPolicy] = {
    "2.0.0": RetrievalBehaviorPolicy(max_spans=6),
    **{
        f"3.0.0 {lane}": lane_policy(lane, max_spans=6)
        for lane in (LEXICAL_SUBSTRING_V1, PROTECTED_EXACT, GROUPED_BM25, CHAR_BIGRAM)
    },
    "4.0.0 two": fused((GROUPED_BM25, 2), (PROTECTED_EXACT, 4), max_spans=6),
    "4.0.0 three": fused((GROUPED_BM25, 2), (PROTECTED_EXACT, 4), (CHAR_BIGRAM, 4), max_spans=12),
    "4.0.0 whole": fused((PROTECTED_EXACT, 3), (GROUPED_BM25, 1)),
}


def requests() -> dict[str, RetrievalRequest]:
    spans, catalog = report()
    flat = CatalogContext(catalog.ordered_refs, catalog.protected_refs, True, 0)
    return {
        "groups": RetrievalRequest("v2 keys median", (), spans, (), catalog),
        "queries": RetrievalRequest("v2", ("keys median", "time per run"), spans, (), catalog),
        "pinned": RetrievalRequest("v2 keys median", (), spans, (spans[20].span_id,), catalog),
        "flat": RetrievalRequest("v2 keys median", (), spans, (), flat),
        "no catalog": RetrievalRequest("v2 keys median", (), spans),
    }


def frozen(request: RetrievalRequest) -> dict[str, JsonValue]:
    """The payload as a host would freeze it: written to JSON text and read back."""
    return cast(dict[str, JsonValue], json.loads(json.dumps(evaluation_case_payload(request))))


@pytest.mark.parametrize("name", list(POLICIES))
def test_evaluating_a_case_selects_what_the_request_itself_selects(name: str) -> None:
    for label, request in requests().items():
        direct = select_retrieval_context(request, POLICIES[name])
        output = evaluate_retrieval(POLICIES[name], frozen(request))
        assert output["selected_refs"] == [s.span_id for s in direct.selected], (name, label)
        assert output["mode"] == direct.mode, (name, label)
        assert output.get("lane") == (dict(direct.provenance) or None), (name, label)


def test_only_a_fused_policy_writes_the_groups_and_lanes_keys() -> None:
    request = requests()["groups"]
    catalog = request.catalog
    assert catalog is not None
    for name, policy in POLICIES.items():
        output = evaluate_retrieval(policy, frozen(request))
        if name.startswith("4.0.0 two") or name == "4.0.0 three":
            assert output["groups"] == len(catalog.groups) > 0
            lanes = cast(list[dict[str, JsonValue]], output["lanes"])
            assert [lane["lane_id"] for lane in lanes][:2] == [GROUPED_BM25, PROTECTED_EXACT]
            assert set(lanes[0]) == {
                "lane_id",
                "lane_version",
                "depth",
                "ranked",
                "credited_refs",
                "also_refs",
            }
        elif name == "4.0.0 whole":
            # The catalog fits: no lane ran, so there is nothing to attribute.
            assert output["groups"] == len(catalog.groups) and "lanes" not in output
        else:
            assert "groups" not in output and "lanes" not in output, name


def test_a_fused_case_without_groups_says_so_in_its_output() -> None:
    request = requests()["no catalog"]
    output = evaluate_retrieval(POLICIES["4.0.0 two"], frozen(request))
    assert output["groups"] == 0 and "lanes" in output
    flat = requests()["flat"]
    assert evaluate_retrieval(POLICIES["4.0.0 two"], frozen(flat))["groups"] == 0


def test_a_case_with_no_groups_is_written_without_the_field() -> None:
    flat = frozen(requests()["flat"])
    assert "groups" not in cast(dict[str, JsonValue], flat["catalog"])
    assert "groups" in cast(dict[str, JsonValue], frozen(requests()["groups"])["catalog"])


def test_the_catalog_of_a_request_survives_the_round_trip_through_a_case() -> None:
    catalog = requests()["groups"].catalog
    assert catalog is not None
    assert catalog_context(catalog_input(catalog)) == catalog


def case_with(groups: list[dict[str, object]]) -> dict[str, JsonValue]:
    payload = frozen(requests()["groups"])
    cast(dict[str, JsonValue], payload["catalog"])["groups"] = cast(JsonValue, groups)
    return payload


def test_groups_that_name_a_span_the_case_does_not_hold_are_refused() -> None:
    bad: tuple[dict[str, object], ...] = (
        {"kind": "SECTION", "title_refs": [], "body_refs": ["span:nowhere"]},
        {"kind": "SECTION", "title_refs": ["span:nowhere"], "body_refs": ["span:rep:0001"]},
    )
    for group in bad:
        with pytest.raises(ValidationError, match="RETRIEVAL_CASE_GROUP_SPAN_UNKNOWN"):
            RetrievalBehaviorInput.model_validate(case_with([group]))


def test_a_span_in_the_body_of_two_groups_is_refused() -> None:
    one: dict[str, object] = {
        "kind": "SECTION",
        "title_refs": [],
        "body_refs": ["span:rep:0001", "span:rep:0002"],
    }
    two: dict[str, object] = {"kind": "LIST", "title_refs": [], "body_refs": ["span:rep:0002"]}
    with pytest.raises(ValidationError, match="RETRIEVAL_CASE_GROUP_BODY_DUPLICATED"):
        RetrievalBehaviorInput.model_validate(case_with([one, two]))
    repeated: dict[str, object] = {
        "kind": "SECTION",
        "title_refs": [],
        "body_refs": ["span:rep:0001"] * 2,
    }
    with pytest.raises(ValidationError, match="RETRIEVAL_CASE_GROUP_BODY_DUPLICATED"):
        RetrievalBehaviorInput.model_validate(case_with([repeated]))


def test_a_title_may_head_several_groups_and_a_valid_case_is_accepted() -> None:
    one: dict[str, object] = {
        "kind": "SECTION",
        "title_refs": ["span:rep:0000"],
        "body_refs": ["span:rep:0001"],
    }
    two: dict[str, object] = {
        "kind": "LIST",
        "title_refs": ["span:rep:0000"],
        "body_refs": ["span:rep:0002"],
    }
    parsed = RetrievalBehaviorInput.model_validate(case_with([one, two]))
    assert parsed.catalog is not None and len(parsed.catalog.groups) == 2


def test_a_lane_policy_with_one_lane_still_rejects_a_second_lane() -> None:
    with pytest.raises(ValueError, match="RETRIEVAL_POLICY_ONE_LANE_SUPPORTED"):
        RetrievalLanePolicy.model_validate(
            {"lanes": [{"lane_id": GROUPED_BM25}, {"lane_id": CHAR_BIGRAM}]}
        )
