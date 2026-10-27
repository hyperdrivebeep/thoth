"""G3 1c: the single-lane selections and evaluation outputs of 3.0.0 do not move.

The digests were taken before lanes could be fused and before a lane could return ranked chunks.
A single-lane policy (3.0.0) selects what it selected in 1b; its evaluation output keeps its keys.
"""

from pydantic import JsonValue
from tests.unit.test_ranked_lanes import policy, report

from thoth.adapters.behavior.registry import evaluate_retrieval
from thoth.domain.behavior_policy import (
    RetrievalCatalogInput,
    RetrievalGroupInput,
)
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.retrieval_lane import (
    CHAR_BIGRAM,
    GROUPED_BM25,
    PROTECTED_EXACT,
    RetrievalRequest,
    select_retrieval_context,
)

SELECTIONS_DIGEST = "58443b9aa0f7712169d220858d4495d4a78e7f5784ac19c3f60f19ec8217ffaa"
EVAL_DIGESTS = {
    GROUPED_BM25: "df7d4f4bb2706c6b925a69ad3b9784872061448c98d899d28e4ca25181f0b6d9",
    CHAR_BIGRAM: "0124a81f6aa71fbc94eac6b8d40b71285160aedee7ee4460151ff750c0a1a441",
}
QUESTIONS = ("v2 keys median", "keys median performance version", "time per run v1")


def test_single_lane_selections_keep_their_order_at_three_limits() -> None:
    spans, catalog = report()
    selected: dict[str, list[str]] = {}
    for question in QUESTIONS:
        for lane in (GROUPED_BM25, CHAR_BIGRAM, PROTECTED_EXACT):
            for limit in (3, 6, 12):
                request = RetrievalRequest(question, (), spans, (), catalog)
                chosen = select_retrieval_context(request, policy(lane, max_spans=limit)).selected
                selected[f"{question}|{lane}|{limit}"] = [s.span_id[-4:] for s in chosen]
    digest = domain_digest("PIN_SELECTIONS", "1.0.0", canonical_payload(selected))
    assert len(selected) == 27 and digest == SELECTIONS_DIGEST


def test_single_lane_evaluation_outputs_keep_their_digests() -> None:
    spans, catalog = report()
    case: dict[str, JsonValue] = {
        "problem": "v2 keys median",
        "evidence": [s.model_dump(mode="json") for s in spans],
        "catalog": RetrievalCatalogInput(
            ordered_refs=catalog.ordered_refs,
            protected_refs=tuple(sorted(catalog.protected_refs)),
            all_observed=catalog.all_observed,
            structure_count=catalog.structure_count,
            groups=tuple(
                RetrievalGroupInput(kind=g.kind, title_refs=g.title_refs, body_refs=g.body_refs)
                for g in catalog.groups
            ),
        ).model_dump(mode="json"),
    }
    for lane, expected in EVAL_DIGESTS.items():
        output = evaluate_retrieval(policy(lane, max_spans=6), case)
        assert set(output) == {
            "selected_refs",
            "excluded_count",
            "selected_characters",
            "mode",
            "evaluator",
            "lane",
        }
        assert domain_digest("PIN_OUTPUT", "1.0.0", canonical_payload(output)) == expected
