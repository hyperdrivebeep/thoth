"""G3 1a: the PROTECTED_EXACT lane ranks by identifiers; the frame around it is unchanged."""

import pytest
from pydantic import JsonValue
from tests.unit.test_retrieval_selection_oracle import span

from thoth.adapters.behavior.registry import evaluate_retrieval
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy, RetrievalLanePolicy
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.query_identifiers import SCANNER_VERSION
from thoth.domain.retrieval_lane import (
    LANE_PROVENANCE,
    LANE_VERSIONS,
    LANES,
    LEXICAL_SUBSTRING_V1,
    PROTECTED_EXACT,
    CatalogContext,
    RetrievalRequest,
    select_retrieval_context,
)

type Policy = RetrievalBehaviorPolicy | RetrievalLanePolicy

EXACT = RetrievalLanePolicy.model_validate({"lanes": [{"lane_id": PROTECTED_EXACT}]})
LEXICAL = RetrievalLanePolicy()
FILLER = " with enough words around it to be an ordinary line"


def req(
    question: str,
    spans: tuple[EvidenceSpan, ...],
    *,
    queries: tuple[str, ...] = (),
    pinned: tuple[str, ...] = (),
    catalog: CatalogContext | None = None,
) -> RetrievalRequest:
    return RetrievalRequest(question, queries, spans, pinned, catalog)


def ids(request: RetrievalRequest, policy: Policy = EXACT) -> list[str]:
    return [s.span_id for s in select_retrieval_context(request, policy).selected]


def test_both_lanes_are_registered_with_a_version_and_only_the_new_one_adds_provenance() -> None:
    # 1b adds lanes to the registry, so the set is no longer exactly these two; each lane still has
    # a registered version and the registry and the version table never drift apart.
    assert {LEXICAL_SUBSTRING_V1, PROTECTED_EXACT} <= set(LANES)
    assert set(LANES) == set(LANE_VERSIONS)
    assert set(LANE_VERSIONS.values()) == {"1.0.0"}
    assert LANE_PROVENANCE[LEXICAL_SUBSTRING_V1] == ()
    assert LANE_PROVENANCE[PROTECTED_EXACT] == (("scanner", SCANNER_VERSION),)


def test_a_policy_may_name_the_new_lane_alone_and_not_together_with_another() -> None:
    assert EXACT.lanes[0].lane_id == PROTECTED_EXACT and EXACT.lanes[0].lane_version == "1.0.0"
    with pytest.raises(ValueError, match="RETRIEVAL_POLICY_ONE_LANE_SUPPORTED"):
        RetrievalLanePolicy.model_validate(
            {"lanes": [{"lane_id": PROTECTED_EXACT}, {"lane_id": LEXICAL_SUBSTRING_V1}]}
        )


def test_a_version_matches_itself_after_a_particle_and_not_a_longer_version() -> None:
    spans = (
        span("a", "released v1.2 as announced" + FILLER, line=1),
        span("b", "released v1.20 as announced" + FILLER, line=2),
        span("c", "released 1.2.0 as announced" + FILLER, line=3),
        span("d", "released 1.2 as announced" + FILLER, line=4),
        span("e", "nothing relevant is written in this line at all", line=5),
    )
    assert ids(req("v1.2와 차이", spans)) == ["span:a", "span:d"]
    assert ids(req("v1.20의 차이", spans)) == ["span:b"]


def test_a_latin_piece_before_a_particle_is_matched_at_identifier_edges() -> None:
    spans = (
        span("a", "GCC에서 컴파일한 결과 비교" + FILLER, line=1),
        span("b", "see gcc.gnu.org for the reference text" + FILLER, line=2),
        span("c", "gcc2 and agcc are other names that appear here" + FILLER, line=3),
        span("d", "the call __builtin_abort () ends the program" + FILLER, line=4),
        span("e", "the program aborts when the check fails" + FILLER, line=5),
    )
    assert ids(req("gcc에서 abort하고 끝남", spans)) == ["span:a", "span:b"]


def test_a_short_title_is_kept_when_it_holds_an_identifier() -> None:
    spans = (
        span("t", "Polars 1.44.1", line=1),
        span("v", "fill_nan 10000 cols 1.557s measured on the same machine", line=2),
        span("x", "Remarks about the machine and its power settings here", line=3),
    )
    question = "Polars 1.44.1 표의 fill_nan 10000 cols 시간"
    assert set(ids(req(question, spans))) == {"span:t", "span:v"}
    assert "span:t" not in ids(req(question, spans), LEXICAL)


def test_a_path_without_an_extension_matches_either_form() -> None:
    spans = (
        span("a", "saved to C:\\data\\report today" + FILLER, line=1),
        span("b", "saved to C:/data/reports today" + FILLER, line=2),
    )
    assert ids(req("C:\\data\\report를 열어", spans)) == ["span:a"]


def test_korean_pieces_match_by_substring_and_function_words_are_ignored() -> None:
    spans = (
        span("a", "이 보고서에는 측정 결과가 적혀 있다" + FILLER, line=1),
        span("b", "여기에서 확인한 값은 아래와 같다" + FILLER, line=2),
    )
    assert ids(req("보고서 결과 gcc에서", spans)) == ["span:a"]
    assert ids(req("에서", spans)) == []


def test_the_frame_still_owns_pinning_the_budget_and_the_whole_catalog() -> None:
    spans = tuple(
        span(f"m{i}", f"entry v1.5 number {i} written down" + FILLER, line=i) for i in range(1, 5)
    )
    spans = (*spans, span("pin", "an unrelated pinned line about something else", line=9))
    pinned = req("v1.5 정리", spans, pinned=("span:pin",))
    two = RetrievalLanePolicy.model_validate(
        {"max_spans": 2, "lanes": [{"lane_id": PROTECTED_EXACT}]}
    )
    assert ids(pinned, two) == ["span:pin", "span:m1"]
    tight = RetrievalLanePolicy.model_validate(
        {"character_budget": 120, "lanes": [{"lane_id": PROTECTED_EXACT}]}
    )
    chosen = select_retrieval_context(pinned, tight).selected
    assert sum(len(s.exact_text) for s in chosen) <= 120
    ordered = tuple(s.span_id for s in spans)
    whole = select_retrieval_context(
        req("v1.5 정리", spans, catalog=CatalogContext(ordered, frozenset(), True, 0)), EXACT
    )
    assert whole.mode == "FULL_ELIGIBLE_EXTRACTED_CATALOG"
    assert [s.span_id for s in whole.selected] == list(ordered) and whole.provenance == ()


def test_with_no_match_only_the_pinned_span_is_returned() -> None:
    spans = (
        span("a", "a line that shares no identifier with the question" + FILLER, line=1),
        span("p", "the pinned line stays first in any case here" + FILLER, line=2),
    )
    assert ids(req("v9.9 필요", spans, pinned=("span:p",))) == ["span:p"]
    assert ids(req("v9.9 필요", spans)) == []


def test_artifacts_take_turns_like_in_the_original_lane() -> None:
    spans = (
        span("a1", "build 3.1 of the first artifact" + FILLER, artifact="artifact:a", line=1),
        span("a2", "build 3.1 again in the first artifact" + FILLER, artifact="artifact:a", line=2),
        span("b1", "build 3.1 of the second artifact" + FILLER, artifact="artifact:b", line=1),
    )
    assert ids(req("3.1 build", spans)) == ["span:a1", "span:b1", "span:a2"]


def test_the_selection_says_which_lane_version_and_scanner_made_it() -> None:
    spans = (span("a", "release 2.5 of the tool" + FILLER, line=1),)
    ranked = select_retrieval_context(req("2.5", spans), EXACT)
    assert dict(ranked.provenance) == {
        "lane_id": PROTECTED_EXACT,
        "lane_version": "1.0.0",
        "scanner": SCANNER_VERSION,
    }
    assert select_retrieval_context(req("2.5", spans), LEXICAL).provenance == ()
    assert select_retrieval_context(req("2.5", spans), RetrievalBehaviorPolicy()).provenance == ()


def test_the_evaluation_output_carries_the_provenance_only_for_the_new_lane() -> None:
    spans = (span("a", "release 2.5 of the tool" + FILLER, line=1),)
    payload: dict[str, JsonValue] = {
        "problem": "2.5",
        "evidence": [s.model_dump(mode="json") for s in spans],
    }
    exact = evaluate_retrieval(EXACT, payload)
    assert exact["selected_refs"] == ["span:a"]
    assert exact["lane"] == {
        "lane_id": PROTECTED_EXACT,
        "lane_version": "1.0.0",
        "scanner": SCANNER_VERSION,
    }
    assert "lane" not in evaluate_retrieval(LEXICAL, payload)


def test_a_policy_naming_a_version_the_code_does_not_register_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans = (span("a", "release 2.5 of the tool" + FILLER, line=1),)
    monkeypatch.setitem(LANE_VERSIONS, PROTECTED_EXACT, "1.1.0")
    with pytest.raises(ValueError, match="RETRIEVAL_LANE_VERSION_UNKNOWN"):
        select_retrieval_context(req("2.5", spans), EXACT)
    # Another lane, and the 2.0.0 policy that names no lane, are not affected.
    assert ids(req("2.5", spans), LEXICAL) == ["span:a"]
    assert ids(req("2.5", spans), RetrievalBehaviorPolicy()) == ["span:a"]
    monkeypatch.setitem(LANE_VERSIONS, LEXICAL_SUBSTRING_V1, "9.0.0")
    with pytest.raises(ValueError, match="RETRIEVAL_LANE_VERSION_UNKNOWN"):
        select_retrieval_context(req("2.5", spans), LEXICAL)
