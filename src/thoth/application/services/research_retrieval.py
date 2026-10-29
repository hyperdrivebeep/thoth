"""Lexical candidates preserve exact identifiers and neighboring original spans."""

from time import perf_counter_ns

from thoth.application.services.behavior_context import SHADOW_SELECTION_FAILED
from thoth.application.services.research_retrieval_policy import (
    current_glossary,
    record_held_selection,
    record_selection,
    replay_retrieval_shadows,
    retrieval_policy,
    retrieval_shadows,
)
from thoth.application.services.research_selection_context import (
    CandidateContext,
    candidate_context,
)
from thoth.domain.behavior_policy import BehaviorPolicyError
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan, connected_retrieval_spans
from thoth.domain.research_execution import ResearchWork
from thoth.domain.retrieval_coverage import RetrievalSelection
from thoth.domain.retrieval_lane import (
    MAX_BLOCK_SPANS,
    LaneSelection,
    RetrievalRequest,
    block_key,
    select_retrieval_context,
)

# Re-exported: callers and tests import the term rules from here.
from thoth.domain.retrieval_lane import search_terms as search_terms
from thoth.domain.retrieval_lane import tokens as tokens
from thoth.ports.artifact_ledger import ArtifactLedgerPort


def text_neighbors(
    span: EvidenceSpan, source: tuple[EvidenceSpan, ...]
) -> tuple[EvidenceSpan, ...]:
    allowed = {item.span_id for item in connected_retrieval_spans(source)}
    siblings = sorted(
        (
            s
            for s in source
            if s.artifact_id == span.artifact_id
            and s.source_version_id == span.source_version_id
            and s.span_id in allowed
        ),
        key=lambda s: (s.locator.page or 0, s.locator.line or 0, s.span_id),
    )
    index = next(i for i, s in enumerate(siblings) if s.span_id == span.span_id)
    near = [*siblings[:1], *siblings[max(0, index - 1) : index + 2]]
    key = block_key(span)
    if key is None:
        return tuple(near)
    block = [
        s
        for s in siblings
        if (s.locator.json_pointer or "") == key
        or (s.locator.json_pointer or "").startswith(f"{key}/")
    ]
    if len(block) > MAX_BLOCK_SPANS:
        return tuple(near)
    return tuple(dict.fromkeys([*near, *block]))


def lexical_selection(
    question: str,
    queries: tuple[str, ...],
    evidence: tuple[EvidenceSpan, ...],
    pinned: tuple[str, ...] = (),
    *,
    context: CandidateContext | None = None,
) -> LaneSelection:
    """Select the shortlist with the active policy, then run each shadow policy on the same request.

    A shadow is journaled as if it had been the active policy; its result never reaches the caller.
    """
    request = RetrievalRequest(
        question,
        queries,
        evidence,
        pinned,
        None if context is None else context.to_catalog(),
        current_glossary(),
    )
    started = perf_counter_ns()
    selection = select_retrieval_context(request, retrieval_policy())
    record_selection(_stage(selection), _inputs(request, selection), selection.selected, started)
    for shadow, shadow_policy in retrieval_shadows():
        started = perf_counter_ns()
        try:
            if shadow_policy is None:
                raise BehaviorPolicyError("BEHAVIOR_POLICY_KIND_MISMATCH")
            replayed = select_retrieval_context(request, shadow_policy)
        except ValueError:
            # A shadow that cannot select is held; the request goes on with the active result.
            record_held_selection(_stage(selection), _inputs(request, selection), started, shadow)
            continue
        except Exception:
            # A shadow that breaks is held under its own reason; it never fails its request. The
            # active selection above is not caught, so a defect there still surfaces.
            record_held_selection(
                _stage(selection),
                _inputs(request, selection),
                started,
                shadow,
                SHADOW_SELECTION_FAILED,
            )
            continue
        record_selection(
            _stage(replayed), _inputs(request, replayed), replayed.selected, started, shadow
        )
    return selection


def _inputs(request: RetrievalRequest, selection: LaneSelection) -> dict[str, object]:
    """What a journaled selection is a function of; a lane that declares its rules adds them."""
    inputs: dict[str, object] = {
        "question": request.question,
        "queries": request.queries,
        "evidence": request.evidence,
    }
    if selection.provenance:
        inputs["lane"] = dict(selection.provenance)
    if selection.routing is not None:
        inputs["routing"] = selection.routing.model_dump(mode="json")
    return inputs


def _stage(selection: LaneSelection) -> str:
    return (
        "FULL_ELIGIBLE_EXTRACTED_CATALOG"
        if selection.mode == "FULL_ELIGIBLE_EXTRACTED_CATALOG"
        else "LEXICAL_CANDIDATES"
    )


def lexical_candidates(
    question: str,
    queries: tuple[str, ...],
    evidence: tuple[EvidenceSpan, ...],
    pinned: tuple[str, ...] = (),
    *,
    context: CandidateContext | None = None,
) -> tuple[EvidenceSpan, ...]:
    return lexical_selection(question, queries, evidence, pinned, context=context).selected


def work_candidates(
    work: ResearchWork,
    queries: tuple[str, ...],
    evidence: tuple[EvidenceSpan, ...],
    artifacts: ArtifactLedgerPort | None = None,
) -> tuple[EvidenceSpan, ...]:
    """The shortlist for a request: its question, its queries and the spans its start row pins."""
    eligible = connected_retrieval_spans(evidence)
    context = None if artifacts is None else candidate_context(eligible, artifacts)
    selection = lexical_selection(
        work.effective_question, queries, evidence, work.pinned_spans, context=context
    )
    result = selection.selected
    work.retrieval_selection = RetrievalSelection(
        evidence,
        eligible,
        tuple(s.span_id for s in result),
        domain_digest("RETRIEVAL_SOURCE", "1.0.0", canonical_payload({"source": evidence}))
        if context is None
        else context.basis_digest,
        domain_digest("RETRIEVAL_POLICY", "1.0.0", canonical_payload(retrieval_policy())),
        selection.mode,
        ("LEGACY_CONTEXT",) if context is None else (*context.limitations, *selection.limitations),
        contextual_refs=() if context is None else context.ordered_refs,
    )
    return result


def pinned_first(
    ranking: tuple[str, ...], candidates: tuple[EvidenceSpan, ...], pinned: tuple[str, ...]
) -> tuple[str, ...]:
    """The ranking with the pinned candidates the model left out put in front of it.

    A pinned span is the starting row's own evidence, so a model's ordering cannot drop it. An id
    the model repeated is left as it was: the assembly still refuses it.
    """
    left_out = tuple(
        span.span_id
        for span in candidates
        if span.span_id in pinned and span.span_id not in ranking
    )
    return (*left_out, *ranking)


def adjacent_packet(
    ranking: tuple[str, ...],
    candidates: tuple[EvidenceSpan, ...],
    source: tuple[EvidenceSpan, ...],
    question: str,
) -> tuple[EvidenceSpan, ...]:
    policy, started = retrieval_policy(), perf_counter_ns()
    allowed = {s.span_id: s for s in candidates}
    if set(ranking) - allowed.keys() or len(set(ranking)) != len(ranking):
        raise ValueError("RERANK_UNKNOWN_OR_DUPLICATE_ID")
    ordered = list(
        dict.fromkeys(
            (
                *(
                    s.span_id
                    for s in candidates
                    if s.span_id in question or s.artifact_id in question
                ),
                *ranking,
            )
        )
    )
    selected: list[EvidenceSpan] = []
    seen: set[str] = set()
    size = 0
    for identifier in ordered:
        span = allowed[identifier]
        # Keep each chunk with preceding/following text, and the first header chunk.
        group = text_neighbors(span, source)
        needed = list({s.span_id: s for s in group if s.span_id not in seen}.values())
        cost = sum(len(s.exact_text) for s in needed)
        if size + cost > policy.character_budget or len(selected) + len(needed) > policy.max_spans:
            continue
        selected.extend(needed)
        seen.update(s.span_id for s in needed)
        size += cost
    result = tuple(selected)
    record_selection("CONTEXT_PACKET", {"ranking": ranking, "source": source}, result, started)
    replay_retrieval_shadows(
        lambda: adjacent_packet(ranking, candidates, source, question),
        {"ranking": ranking, "source": source},
    )
    return result
