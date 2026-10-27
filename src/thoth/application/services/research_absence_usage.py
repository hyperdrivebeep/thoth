"""Admit optional catalog checks under the existing resource-use guard and a smaller budget."""

from collections import defaultdict
from collections.abc import Callable

from thoth.domain.absence_claim import AbsenceRun
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.resource_scope import (
    RESOURCE_SCOPE_USE_LIMIT,
    ResourceScopeError,
    current_resource_uses,
)
from thoth.ports.source_version_catalog import SourceVersionCatalogPort

# A query can check both current and previous results. Bound this optional producer to one
# quarter of the existing guard and leave at least half for the ordinary research/publication path.
ABSENCE_CATALOG_USE_LIMIT = RESOURCE_SCOPE_USE_LIMIT // 4
ABSENCE_RESERVED_RESOURCE_USES = RESOURCE_SCOPE_USE_LIMIT // 2
ABSENCE_SCOPE_USAGE_LIMIT = "ABSENCE_SCOPE_USAGE_LIMIT"


def _admit_spans(
    state: AbsenceRun,
    candidates: tuple[EvidenceSpan, ...],
    read: Callable[[str], EvidenceSpan | None],
) -> tuple[frozenset[str], tuple[str, ...]]:
    admitted: set[str] = set()
    reasons: set[str] = set()
    for span in candidates:
        if (
            span.span_id not in state.catalog_usage_refs
            and len(state.catalog_usage_refs) >= ABSENCE_CATALOG_USE_LIMIT
        ):
            reasons.add(ABSENCE_SCOPE_USAGE_LIMIT)
            continue
        uses = current_resource_uses()
        already_read = uses is not None and any(
            use.project_id == span.project_id
            and use.resource_ref == span.span_id
            and use.capability == "READ"
            for use in uses
        )
        if (
            uses is not None
            and not already_read
            and len(uses) >= RESOURCE_SCOPE_USE_LIMIT - ABSENCE_RESERVED_RESOURCE_USES
        ):
            reasons.add(ABSENCE_SCOPE_USAGE_LIMIT)
            continue
        # This uses the existing scoped reader, including per-reference authorization and usage.
        # Literal matching still uses the frozen, stored text, never corrections or raw objects.
        state.catalog_usage_refs.add(span.span_id)
        try:
            current = read(span.span_id)
        except ResourceScopeError as exc:
            if exc.code == "RESOURCE_SCOPE_USAGE_LIMIT":
                reasons.add(ABSENCE_SCOPE_USAGE_LIMIT)
                break
            if exc.code in {
                "RESOURCE_ACCESS_DENIED",
                "RESOURCE_SCOPE_UNKNOWN",
                "RESOURCE_REFERENCE_UNRESOLVED",
                "RESOURCE_LINEAGE_UNKNOWN",
            }:
                reasons.add("ABSENCE_SCOPE_SOURCE_UNAVAILABLE")
                continue
            raise
        if current == span:
            admitted.add(span.span_id)
        else:
            reasons.add("ABSENCE_SOURCE_BASIS_CHANGED")
    return frozenset(admitted), tuple(sorted(reasons))


def admit_absence_catalog(
    state: AbsenceRun,
    candidates: tuple[EvidenceSpan, ...],
    read: Callable[[str], EvidenceSpan | None],
    catalog: SourceVersionCatalogPort | None = None,
) -> tuple[frozenset[str], tuple[str, ...]]:
    if catalog is None:
        return _admit_spans(state, candidates, read)
    groups: dict[tuple[str, str, str], list[EvidenceSpan]] = defaultdict(list)
    for span in candidates:
        groups[span.project_id, span.artifact_id, span.source_version_id].append(span)
    admitted: set[str] = set()
    reasons: set[str] = set()
    for (project, artifact, version), spans in groups.items():
        uses = current_resource_uses()
        if (
            uses is not None
            and len(uses) >= RESOURCE_SCOPE_USE_LIMIT - ABSENCE_RESERVED_RESOURCE_USES
        ) or (
            artifact not in state.catalog_usage_refs
            and len(state.catalog_usage_refs) >= ABSENCE_CATALOG_USE_LIMIT
        ):
            reasons.add(ABSENCE_SCOPE_USAGE_LIMIT)
            continue
        state.catalog_usage_refs.add(artifact)
        try:
            current = {
                s.span_id: s for s in catalog.read_evidence_version(project, artifact, version)
            }
        except ResourceScopeError as exc:
            if exc.code in {
                "RESOURCE_CATALOG_READ_UNSUPPORTED",
                "RESOURCE_CATALOG_SCOPE_NOT_UNIFORM",
            }:
                refs, skipped = _admit_spans(state, tuple(spans), read)
                admitted.update(refs)
                reasons.update(skipped)
                continue
            if exc.code == "RESOURCE_SCOPE_USAGE_LIMIT":
                reasons.add(ABSENCE_SCOPE_USAGE_LIMIT)
                continue
            raise
        for span in spans:
            if current.get(span.span_id) == span:
                admitted.add(span.span_id)
            else:
                reasons.add("ABSENCE_SOURCE_BASIS_CHANGED")
    return frozenset(admitted), tuple(sorted(reasons))
