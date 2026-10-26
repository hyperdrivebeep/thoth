"""Scoped assembly/cache orchestration with one per-attempt selection snapshot."""

from collections.abc import Callable
from dataclasses import replace
from time import perf_counter_ns

from thoth.application.services.research_context_assembler import assemble_context
from thoth.application.services.research_retrieval import pinned_first
from thoth.application.services.research_retrieval_policy import record_selection, retrieval_policy
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_bundle import ContextAssembly
from thoth.domain.research_execution import ResearchWork
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.model import ModelExecutionHold


def assemble_research_context(
    work: ResearchWork,
    ranking: tuple[str, ...],
    candidates: tuple[EvidenceSpan, ...],
    source: tuple[EvidenceSpan, ...],
    artifacts: ArtifactLedgerPort,
    structures: Callable[[tuple[EvidenceSpan, ...]], list[dict[str, object]]],
) -> ContextAssembly:
    started = perf_counter_ns()
    work.boundary.check()
    ranking = pinned_first(ranking, candidates, work.pinned_spans)
    key = domain_digest(
        "CONTEXT_ASSEMBLY_INPUT",
        "2.0.0",
        canonical_payload(
            {
                "ranking": ranking,
                "candidates": candidates,
                "source": source,
                "structure_basis": structures(source),
                "policy": retrieval_policy(),
            }
        ),
    )
    prior = work.preprocessing_cache.get(key)
    if isinstance(prior, ContextAssembly):
        record_selection("CONTEXT_PREPROCESSING_REUSE", {"basis": key}, prior.evidence, started)
        result = prior.model_copy(
            update={
                "wave": prior.wave.model_copy(
                    update={
                        "cache_hit": True,
                        "new_information_count": 0,
                        "termination": prior.wave.termination
                        if prior.wave.omitted_anchor_refs or prior.wave.missing_structure_refs
                        else "NO_NEW_INFORMATION",
                    }
                )
            }
        )
    else:
        result = assemble_context(ranking, candidates, source, artifacts)
        work.preprocessing_cache[key] = result
    if work.retrieval_selection is not None:
        omitted_context = set(work.retrieval_selection.contextual_refs) & set(
            result.wave.omitted_anchor_refs
        )
        if set(work.pinned_spans) & omitted_context:
            raise ModelExecutionHold("PINNED_CONTEXT_NOT_ADMITTED")
        work.retrieval_selection = replace(
            work.retrieval_selection,
            anchor_refs=ranking,
            omitted_anchor_refs=result.wave.omitted_anchor_refs,
            limitations=tuple(
                sorted(
                    set(
                        (
                            *work.retrieval_selection.limitations,
                            *(("REQUIRED_CONTEXT_NOT_ADMITTED",) if omitted_context else ()),
                            *(
                                (result.wave.termination,)
                                if result.wave.omitted_anchor_refs
                                or result.wave.missing_structure_refs
                                else ()
                            ),
                            *(reason for bundle in result.bundles for reason in bundle.limitations),
                        )
                    )
                )
            ),
        )
    return result
