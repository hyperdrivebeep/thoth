"""Pure Revision read responses and traversal; provenance and publication stay in handlers."""

from pydantic import JsonValue

from thoth.domain.control_record import ControlRecord
from thoth.domain.revision import SemanticDiffEntry, SemanticRevision


def revision_ancestor_digests(
    revisions: tuple[SemanticRevision, ...], root: str, depth: int
) -> set[str]:
    by_digest = {item.revision_digest: item for item in revisions}
    seen: set[str] = set()
    frontier = [(root, 0)]
    while frontier:
        digest, level = frontier.pop(0)
        if digest in seen or level > depth:
            continue
        seen.add(digest)
        item = by_digest.get(digest)
        if item is not None:
            frontier.extend((parent, level + 1) for parent in item.parent_revision_digests)
    return seen


def revision_diff_view(changes: tuple[SemanticDiffEntry, ...]) -> dict[str, JsonValue]:
    return {
        "diff": [item.model_dump(mode="json") for item in changes],
        "algorithm": "THOTH_SEMANTIC_DIFF",
        "version": "1.0.0",
        "affected_paths": [item.path for item in changes],
        "noncanonical_warning": True,
    }


def revision_graph_view(
    revisions: tuple[SemanticRevision, ...], branches: tuple[ControlRecord, ...]
) -> dict[str, JsonValue]:
    return {
        "nodes": [item.model_dump(mode="json") for item in revisions],
        "edges": [
            {
                "from": parent,
                "to": item.revision_digest,
                "relation": "PARENT",
            }
            for item in revisions
            for parent in item.parent_revision_digests
        ],
        "branches": [item.model_dump(mode="json") for item in branches],
    }


def revision_impact_view(
    change_set: ControlRecord | None, downstream: tuple[str, ...]
) -> dict[str, JsonValue]:
    return {
        "change_set": None if change_set is None else change_set.model_dump(mode="json"),
        "explicit_dependencies": list(downstream),
        "propagation_rules": "TYPED_DEPENDENCY_GRAPH",
        "affected_projections": list(downstream),
        "cycles": [],
        "recomputation_plan": list(downstream),
    }
