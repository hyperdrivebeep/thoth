"""Shared v2 model view. Canonical spans/hashes remain on the server, never rewritten."""

import json
from typing import cast

from thoth.domain.canonical import canonical_payload
from thoth.domain.model import ContextPack

PROJECTION_GUIDE = (
    "CONTEXT_PROJECTION_V2: quote source_index selects sources; document_index selects "
    "documents and artifact_index selects artifacts. Shared entries retain exact source identity "
    "and state, not absent metadata. Context refs describe observed positions, not semantic "
    "support. retrieval_coverage describes only this authorized extracted-source input, not "
    "a search of the entire document or world. Canonical quote hashes/basis are verified by "
    "the server; cite the supplied span IDs.\n"
    "Integer quote refs index spans; integer context refs index context_regions. "
    "ALL_SUPPLIED_QUOTES means every supplied quote. The output still uses canonical span_id "
    "strings, never integer indexes. Context line ranges are one-based/end-exclusive; character "
    "ranges are decoded-file Unicode codepoints. Positional relations are OBSERVED_POSITION.\n"
)


def project_context_fields(context: ContextPack) -> dict[str, object]:
    return {
        key: getattr(context, key)
        for key in (
            "case_id",
            "project_id",
            "object_id",
            "problem",
            "criteria",
            "sufficiency",
            "input_head_set_digest",
            "policy_hints",
            "previous_portfolio",
            "previous_action_plan",
            "candidate_portfolio",
            "canonical_hypotheses",
            "canonical_portfolio",
            "canonical_actions",
            "canonical_action_plan",
            "canonical_test_assessments",
            "unresolved_research_refs",
            "research_context",
        )
    }


def uses_compact_context(context: ContextPack) -> bool:
    return context.research_context.get("context_projection_version") == "2.0.0"


def compact_model_context(context: ContextPack) -> tuple[str, str]:
    project = cast(
        dict[str, object], json.loads(canonical_payload(project_context_fields(context)))
    )
    research = cast(dict[str, object], project["research_context"])
    quote_ids = [s.span_id for s in context.evidence]
    quote_indexes = {identifier: i for i, identifier in enumerate(quote_ids)}
    coverage_value = research.get("retrieval_coverage")
    if isinstance(coverage_value, dict):
        coverage = cast(dict[str, object], coverage_value)
        if coverage.get("input_span_refs") != quote_ids:
            raise ValueError("RETRIEVAL_COVERAGE_INPUT_MISMATCH")
        coverage.pop("input_span_refs")
        coverage["input_quote_set"] = "ALL_SUPPLIED_QUOTES"
    rankable = research.pop("ranking_candidate_refs", None)
    if rankable is not None:
        research["ranking_candidate_quote_refs"] = [
            quote_indexes[k] for k in cast(list[str], rankable)
        ]
    documents: list[dict[str, object]] = []
    document_indexes: dict[tuple[str, str], int] = {}

    def document_index(artifact: str, version: str) -> int:
        key = (artifact, version)
        if key not in document_indexes:
            document_indexes[key] = len(documents)
            documents.append({"artifact_id": artifact, "source_version_id": version})
        return document_indexes[key]

    def intern_document(row: dict[str, object]) -> int:
        index = document_index(cast(str, row["artifact_id"]), cast(str, row["source_version_id"]))
        existing = documents[index]
        if any(k in existing and existing[k] != v for k, v in row.items()):
            raise ValueError("CONTEXT_SOURCE_METADATA_CONFLICT")
        existing.update(row)
        return index

    packet_value = research.get("source_packet")
    artifacts: list[dict[str, object]] = []
    if isinstance(packet_value, dict):
        packet = cast(dict[str, object], packet_value)
        artifacts = cast(list[dict[str, object]], packet.pop("artifacts", []))
        packet["artifact_refs"] = list(range(len(artifacts)))
        packet["structure_refs"] = [
            intern_document(row)
            for row in cast(list[dict[str, object]], packet.pop("structures", []))
        ]
    if isinstance(research.get("source_overview"), list):
        research["source_overview"] = [
            {"document_ref": intern_document(row)}
            for row in cast(list[dict[str, object]], research["source_overview"])
        ]
    for index, artifact in enumerate(artifacts):
        for document in documents:
            if document["artifact_id"] == artifact["artifact_id"]:
                document["artifact_index"] = index
    bundle_value = research.get("context_bundle")
    if isinstance(bundle_value, dict):
        bundle = cast(dict[str, object], bundle_value)
        region_indexes: dict[str, int] = {
            cast(str, r["region_id"]): i
            for i, r in enumerate(cast(list[dict[str, object]], bundle.get("context_regions", [])))
        }
        for group in cast(list[dict[str, object]], bundle.get("bundles", [])):
            for source_key, target_key in (
                ("span_refs", "quote_refs"),
                ("anchor_span_refs", "anchor_quote_refs"),
            ):
                group[target_key] = [
                    quote_indexes[k] for k in cast(list[str], group.pop(source_key))
                ]
            if "context_refs" in group:
                group["context_refs"] = [
                    region_indexes[k] for k in cast(list[str], group["context_refs"])
                ]
            group["document_ref"] = document_index(
                cast(str, group.pop("artifact_id")), cast(str, group.pop("source_version_id"))
            )
            # bundle_id already names the same digest; canonical data retains both fields.
            digest = group.get("basis_digest")
            if group.get("bundle_id") == f"bundle:{digest}":
                group.pop("basis_digest")
        for region in cast(list[dict[str, object]], bundle.get("context_regions", [])):
            region["member_count"] = len(cast(list[str], region.pop("node_refs", [])))
            region.pop("container_path", None)
            for key in ("heading_refs", "leading_refs"):
                region[key] = [region_indexes[k] for k in cast(list[str], region[key])]
            region["lines"] = [region.pop("line_start"), region.pop("line_end")]
            region["characters"] = [region.pop("char_start"), region.pop("char_end")]
            region.pop("coordinate_basis")
            region.pop("relation_basis")
            for key in ("marker", "info", "heading_level"):
                if not region.get(key):
                    region.pop(key, None)
            if region.get("closure") == "NOT_APPLICABLE":
                region.pop("closure")
    states: list[dict[str, object]] = []
    state_indexes: dict[str, int] = {}
    quotes: list[dict[str, object]] = []
    for span in context.evidence:
        state: dict[str, object] = {
            "document_index": document_index(span.artifact_id, span.source_version_id),
            "extraction_method": span.extraction_method,
            "authority_state": span.authority_state,
            "verification_state": span.verification_state,
            "cutoff_state": span.cutoff_state,
        }
        key = canonical_payload(state).decode()
        if key not in state_indexes:
            state_indexes[key] = len(states)
            states.append(state)
        quotes.append(
            {
                "span_id": span.span_id,
                "source_index": state_indexes[key],
                "locator": span.locator.model_dump(mode="json", exclude_none=True),
                "exact_text": span.exact_text,
            }
        )
    return canonical_payload(project).decode(), canonical_payload(
        {
            "schema_version": "2.0.0",
            "sources": states,
            "documents": documents,
            "artifacts": artifacts,
            "spans": quotes,
        }
    ).decode()
