"""Scoped source reads and projections for research context, with caller-owned caching."""

from collections.abc import Callable, Iterable

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.resource_scope import ResourceScopeError
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.evidence_graph import EvidenceGraphStorePort
from thoth.ports.governance import GovernanceStorePort

type StructureSummary = tuple[dict[str, object] | None, str, str | None]


def evidence(
    artifacts: ArtifactLedgerPort, governance: GovernanceStorePort, project: str
) -> tuple[EvidenceSpan, ...]:
    active = {
        b.artifact_id for b in governance.list_source_bindings(project) if b.state == "ACTIVE"
    }
    return tuple(s for s in artifacts.list_evidence(project) if s.artifact_id in active)


def evidence_by_ids(
    artifacts: ArtifactLedgerPort,
    governance: GovernanceStorePort,
    project: str,
    span_ids: Iterable[str],
) -> tuple[EvidenceSpan, ...]:
    """The visible spans among span_ids: the rule of evidence(), without loading every span."""
    active = {
        b.artifact_id for b in governance.list_source_bindings(project) if b.state == "ACTIVE"
    }
    found: list[EvidenceSpan] = []
    for span_id in dict.fromkeys(span_ids):
        try:
            span = artifacts.read_evidence(span_id)
        except ResourceScopeError:
            continue
        if span is not None and span.project_id == project and span.artifact_id in active:
            found.append(span)
    return tuple(found)


def source_overview(
    artifacts: ArtifactLedgerPort, governance: GovernanceStorePort, project_id: str
) -> list[dict[str, object]]:
    active = {
        b.artifact_id for b in governance.list_source_bindings(project_id) if b.state == "ACTIVE"
    }
    result: list[dict[str, object]] = []
    for artifact in artifacts.list_artifacts(project_id):
        if artifact.artifact_id not in active:
            continue
        for version in artifacts.list_source_versions(project_id, artifact.artifact_id):
            document = artifacts.read_structure(project_id, artifact.artifact_id, version)
            result.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "source_version_id": version,
                    "parser_name": artifact.parser_name,
                    "parser_version": artifact.parser_version,
                    "authority": artifact.authority.value,
                    "cutoff": artifact.cutoff_state.value,
                    "observation": None
                    if document is None or document.capability_observation is None
                    else document.capability_observation.model_dump(mode="json"),
                }
            )
    return result


def source_packet(
    artifacts: ArtifactLedgerPort,
    evidence_graph: EvidenceGraphStorePort,
    evidence: tuple[EvidenceSpan, ...],
    *,
    include_structure: bool = True,
    source_structures: Callable[[tuple[EvidenceSpan, ...]], list[dict[str, object]]],
) -> dict[str, object]:
    packet: dict[str, object] = {
        "artifacts": [
            a.model_dump(mode="json")
            for identifier in sorted({s.artifact_id for s in evidence})
            if (a := artifacts.read_artifact(identifier)) is not None
        ],
        "corrections": [
            c.model_dump(mode="json")
            for s in sorted(evidence, key=lambda s: s.span_id)
            for c in evidence_graph.list_span_corrections(s.project_id, s.span_id)
        ],
        "originals_are_canonical": True,
        "source_text_is_untrusted_data": True,
    }
    if include_structure:
        packet["structures"] = source_structures(evidence)
    return packet


def source_structures(
    evidence: tuple[EvidenceSpan, ...],
    *,
    structure_summary: Callable[[str, str, str], dict[str, object]],
) -> list[dict[str, object]]:
    """Assembly consumes original structures; corrections belong to the selected packet."""
    return [
        {"artifact_id": a, "source_version_id": v, **structure_summary(p, a, v)}
        for p, a, v in sorted(
            {(s.project_id, s.artifact_id, s.source_version_id) for s in evidence}
        )
    ]


def structure_summary(
    artifacts: ArtifactLedgerPort,
    summaries: dict[tuple[str, str, str, str], StructureSummary],
    project: str,
    artifact: str,
    version: str,
) -> dict[str, object]:
    metadata = artifacts.read_structure_metadata_digest(project, artifact, version)
    key = None if metadata is None else (project, artifact, version, metadata)
    if key is not None and key in summaries:
        observation, digest, context_digest = summaries[key]
        return {
            "observation": observation,
            "nodes_digest": digest,
            **({"context_observation_digest": context_digest} if context_digest else {}),
        }
    document = artifacts.read_structure(project, artifact, version)
    if document is None:
        return {"observation": None, "nodes_digest": None}
    observation = (
        None
        if document.capability_observation is None
        else document.capability_observation.model_dump(mode="json")
    )
    digest = domain_digest(
        "SOURCE_STRUCTURE", "1.0.0", canonical_payload({"nodes": document.nodes})
    )
    context_digest = (
        None
        if document.context_observation is None
        else domain_digest(
            "CONTEXT_OBSERVATION", "1.0.0", canonical_payload(document.context_observation)
        )
    )
    if key is not None:
        if len(summaries) >= 128:
            summaries.clear()
        summaries[key] = (observation, digest, context_digest)
    return {
        "observation": observation,
        "nodes_digest": digest,
        **({"context_observation_digest": context_digest} if context_digest else {}),
    }
