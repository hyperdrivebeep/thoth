"""G3 2: a research request reads the term list only for a policy that names it.

A shadow policy that needs a list it cannot have is held and the request goes on; an active policy
that needs one is refused, as the active selection is never caught.
"""

import pytest
from tests.unit.test_glossary_lane import glossary, policy
from tests.unit.test_retrieval_policy_v3_research_path import snapshot
from tests.unit.test_retrieval_selection_oracle import span

from thoth.application.services.behavior_context import BehaviorWorkContext, behavior_work_scope
from thoth.application.services.research_retrieval import lexical_candidates
from thoth.application.services.research_retrieval_policy import current_glossary
from thoth.domain.behavior_execution import BehaviorUse
from thoth.domain.behavior_policy import RetrievalBehaviorPolicy, RetrievalPolicy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.glossary import Glossary, build_glossary, make_entry
from thoth.domain.retrieval_lane import GLOSSARY_BM25, GROUPED_BM25, PROTECTED_EXACT

SOURCE = (
    span("a", "the median of the run is reported in the first block of the text", line=1),
    span("b", "alpha reading two in the first block of the source text", line=2),
    span("c", "a closing remark that does not match the question at all", line=3),
)
QUESTION = "중앙값이 뭐야"
ACTIVE = RetrievalBehaviorPolicy()
WITH_GLOSSARY = policy((GLOSSARY_BM25, 2), (GROUPED_BM25, 2), max_spans=1)
WITHOUT_GLOSSARY = policy((GROUPED_BM25, 2), (PROTECTED_EXACT, 2), max_spans=1)
HELD_OUTPUT = domain_digest(
    "BEHAVIOR_POLICY_HELD", "1.0.0", canonical_payload({"reason_code": "SHADOW_SELECTION_HELD"})
)


class Reader:
    """A glossary reader that counts how often it is asked."""

    def __init__(self, held: Glossary | None, *, fail: bool = False) -> None:
        self.asked, self._held, self._fail = 0, held, fail

    def __call__(self) -> Glossary | None:
        self.asked += 1
        if self._fail:
            raise OSError("the file cannot be read")
        return self._held


def run(
    reader: Reader | None, active: RetrievalPolicy, *shadows: RetrievalPolicy
) -> tuple[list[str], list[BehaviorUse]]:
    held = tuple(snapshot(p, "SHADOW") for p in shadows)
    context = BehaviorWorkContext("p", (snapshot(active),), shadows=held, glossary_reader=reader)
    with behavior_work_scope(context):
        chosen = lexical_candidates(QUESTION, (), SOURCE)
    return [s.span_id for s in chosen], context.uses


def test_a_request_with_no_glossary_policy_does_not_read_the_file() -> None:
    reader = Reader(glossary())
    plain, _ = run(None, ACTIVE)
    chosen, uses = run(reader, ACTIVE, WITHOUT_GLOSSARY, RetrievalBehaviorPolicy(max_spans=1))
    assert chosen == plain and reader.asked == 0
    assert [u.operation for u in uses] == ["EVIDENCE_SELECTION"] * 3
    with behavior_work_scope(BehaviorWorkContext("p", (snapshot(ACTIVE),), glossary_reader=reader)):
        assert current_glossary() is None
    assert reader.asked == 0
    assert current_glossary() is None


def test_a_shadow_that_names_the_glossary_reads_it_and_is_replayed() -> None:
    reader = Reader(glossary())
    plain, _ = run(None, ACTIVE)
    chosen, uses = run(reader, ACTIVE, WITH_GLOSSARY)
    assert chosen == plain and reader.asked == 1
    assert [u.operation for u in uses] == ["EVIDENCE_SELECTION", "EVIDENCE_SELECTION"]


def test_a_shadow_is_held_when_the_glossary_is_missing_unreadable_or_of_another_digest() -> None:
    other = build_glossary([make_entry("중앙값", "average", "perf", "HIGH")])
    plain, _ = run(None, ACTIVE)
    for reader in (None, Reader(None), Reader(glossary(), fail=True), Reader(other)):
        chosen, uses = run(reader, ACTIVE, WITH_GLOSSARY)
        assert chosen == plain
        assert [u.operation for u in uses] == ["EVIDENCE_SELECTION", "POLICY_HELD"]
        assert uses[1].output_digest == HELD_OUTPUT
        assert uses[1].input_digest == uses[0].input_digest


def test_an_active_policy_that_names_a_glossary_it_cannot_have_is_refused() -> None:
    with pytest.raises(ValueError, match="RETRIEVAL_GLOSSARY_UNAVAILABLE"):
        run(Reader(None), WITH_GLOSSARY)
    # With the glossary the active policy selects, and the glossary lane is credited.
    chosen, uses = run(Reader(glossary()), WITH_GLOSSARY)
    assert chosen[0] == "span:a" and [u.operation for u in uses] == ["EVIDENCE_SELECTION"]
