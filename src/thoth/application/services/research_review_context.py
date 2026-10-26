"""Pure input views for ranking, semantic review and separate adjudication."""

from thoth.application.services.research_role_context import bundle_view
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_bundle import ContextAssembly
from thoth.domain.evidence_requirements import EvidenceRanking, ReviewProposal
from thoth.domain.model import ContextPack
from thoth.domain.research_execution import ResearchWork
from thoth.domain.research_request import RevisionRef

# v6 marks the repaired review and adjudication task sentences; the rules are unchanged.
_PROMPT_VERSIONS = {
    ModelRole.SEMANTIC_REVIEWER: "semantic_reviewer.v6",
    ModelRole.REVIEW_ADJUDICATOR: "review_adjudicator.v6",
}


def research_prompt_version(role: ModelRole) -> str:
    return _PROMPT_VERSIONS.get(role, f"{role.value.lower()}.v5")


def ranking_context(
    context: ContextPack, work: ResearchWork, ref: RevisionRef, candidates: tuple[EvidenceSpan, ...]
) -> ContextPack:
    return context.model_copy(
        update={
            "evidence": candidates,
            "research_context": {
                **work.context,
                "context_bundle": None,
                "requirement_set_ref": ref.model_dump(mode="json"),
                "task": "Rank relevance; preserve exact IDs, Korean "
                "terms, abbreviations and units. Relevance does not imply support. "
                "Return unselected "
                "required information as explicit gaps. JSON-pointer table indexes are "
                "not printed table numbers. Structure is expanded after your ranking.",
            },
        }
    )


def semantic_review_context(
    context: ContextPack, work: ResearchWork, ranked: EvidenceRanking
) -> ContextPack:
    return context.model_copy(
        update={
            "evidence": work.evidence,
            "research_context": {
                **work.context,
                "task": "Review each exact requirement against target, conditions, time, "
                "contrary evidence and adjacent original context. "
                "Record conflicts in scoped_conflicts with exact requirement_ids and span IDs; "
                "leave legacy conflicts empty. Distinguish current targets "
                "from optional checks. "
                "Use only supplied span IDs in evidence_refs and applicability_basis; "
                "put prose in explanation. For record-summary requests, assess what the record "
                "states rather than treating the statement as independently proven "
                "world truth. "
                "Official "
                "authority or a citation alone is not support. Do not invent support edges. "
                "BOUND exemptions require governing authority, never model discretion.",
                "omitted_required_information": ranked.omitted_required_information,
            },
        }
    )


def contextual_ranking(
    context: ContextPack, candidates: tuple[EvidenceSpan, ...], assembly: ContextAssembly
) -> ContextPack:
    admitted = {s.span_id for s in assembly.evidence}
    return context.model_copy(
        update={
            "evidence": assembly.evidence,
            "research_context": {
                **context.research_context,
                "context_bundle": bundle_view(assembly),
                "ranking_candidate_refs": tuple(
                    s.span_id for s in candidates if s.span_id in admitted
                ),
            },
        }
    )


def adjudication_context(
    review_context: ContextPack, candidate: ReviewProposal, ref: RevisionRef
) -> ContextPack:
    return review_context.model_copy(
        update={
            "research_context": {
                **review_context.research_context,
                "candidate": candidate.model_dump(mode="json"),
                "requirement_set_ref": ref.model_dump(mode="json"),
                "task": "Separately adjudicate this candidate and the completeness of "
                "question decomposition, including each scoped conflict's relevance and "
                "resolution. Bind each "
                "conflict decision to requirement_set_ref.revision_digest and supplied "
                "evidence basis_refs. Unproven scope/resolution is INCONCLUSIVE. "
                "Check exact evidence and conditions. "
                "N/A research checks need request-bound source basis. "
                "You have no authority to waive a BOUND rule. "
                "Missing or uncertain means INCONCLUSIVE.",
            }
        }
    )
