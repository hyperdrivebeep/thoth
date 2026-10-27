"""The one way an evaluation case is made from a retrieval request.

No product code freezes evaluation cases: a case is a host-configured payload. This module turns
the request the research path itself builds (question, queries, evidence, pins and the structure
context) into that payload, and back into the context the selection function reads, so a case
carries the same groups a live request had and the evaluator reads them the same way. A case with
no groups is written without the field, so its bytes and digest are what they always were.
"""

from pydantic import JsonValue

from thoth.domain.behavior_policy import (
    RetrievalBehaviorInput,
    RetrievalCatalogInput,
    RetrievalGroupInput,
)
from thoth.domain.retrieval_lane import CatalogContext, RetrievalRequest
from thoth.domain.structure_groups import CatalogGroup


def catalog_input(context: CatalogContext) -> RetrievalCatalogInput:
    return RetrievalCatalogInput(
        ordered_refs=context.ordered_refs,
        protected_refs=tuple(sorted(context.protected_refs)),
        all_observed=context.all_observed,
        structure_count=context.structure_count,
        groups=tuple(
            RetrievalGroupInput(kind=g.kind, title_refs=g.title_refs, body_refs=g.body_refs)
            for g in context.groups
        ),
    )


def catalog_context(catalog: RetrievalCatalogInput) -> CatalogContext:
    return CatalogContext(
        catalog.ordered_refs,
        frozenset(catalog.protected_refs),
        catalog.all_observed,
        catalog.structure_count,
        tuple(CatalogGroup(g.kind, g.title_refs, g.body_refs) for g in catalog.groups),
    )


def evaluation_case_payload(request: RetrievalRequest) -> dict[str, JsonValue]:
    """The case payload of a request; evaluating it selects what the request itself selects."""
    case = RetrievalBehaviorInput(
        problem=request.question,
        evidence=request.evidence,
        pinned_refs=request.pinned_refs,
        queries=request.queries,
        catalog=None if request.catalog is None else catalog_input(request.catalog),
    )
    return case.model_dump(mode="json")


def request_of_case(case: RetrievalBehaviorInput) -> RetrievalRequest:
    """The request an evaluation case stands for; a pin named twice is read once."""
    return RetrievalRequest(
        case.problem,
        case.queries,
        case.evidence,
        tuple(dict.fromkeys((*case.forced_refs, *case.pinned_refs))),
        None if case.catalog is None else catalog_context(case.catalog),
    )
