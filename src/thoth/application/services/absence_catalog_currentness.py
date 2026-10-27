"""Recheck catalog-only dependencies through their recorded source-owner READs."""

from thoth.domain.absence_claim import AbsenceAssessments, AbsenceSourceRef
from thoth.domain.research_basis import ResearchResultBasis
from thoth.domain.research_request import CurrentResultPayload
from thoth.domain.resource_scope import ResourceUse


def catalog_only_dependencies(
    basis: ResearchResultBasis, manifest: CurrentResultPayload | None
) -> dict[str, AbsenceSourceRef]:
    if manifest is None or manifest.result.get("absence_assessments") is None:
        return {}
    observed = AbsenceAssessments.model_validate(manifest.result["absence_assessments"])
    if observed.request_ref != basis.request_ref or manifest.request_ref != basis.request_ref:
        raise ValueError("ABSENCE_CURRENTNESS_REQUEST_MISMATCH")
    uses = tuple(ResourceUse.model_validate(u) for u in manifest.resource_uses)
    reads = {
        u.resource_ref
        for u in uses
        if u.project_id == basis.request_ref.project_id and u.capability == "READ"
    }
    individual = {
        *manifest.source_refs,
        *(u.resource_ref for u in uses),
        *(
            s.span_id
            for scope in observed.scope_sets
            if scope.kind == "ACTUAL_ROLE_INPUT"
            for s in scope.members
        ),
        *(
            ref
            for item in observed.items
            for outcome in (item.a_result, item.b_result)
            for ref in outcome.witness_refs
        ),
    }
    members: dict[str, AbsenceSourceRef] = {}
    for scope in observed.scope_sets:
        if scope.kind == "ALLOWED_EXTRACTED_AT_REQUEST":
            checked = set(scope.checked_refs)
            members.update((s.span_id, s) for s in scope.members if s.span_id in checked)
    grouped: dict[str, AbsenceSourceRef] = {}
    for ref in basis.source_basis:
        original = members.get(ref.span_id or "")
        if (
            original is not None
            and original.span_id not in individual
            and original.artifact_id in reads
        ):
            if (ref.artifact_id, ref.source_version_id, ref.text_sha256) != (
                original.artifact_id,
                original.source_version_id,
                original.text_sha256,
            ):
                raise ValueError("ABSENCE_CURRENTNESS_SOURCE_MISMATCH")
            grouped[original.span_id] = original
    return grouped
