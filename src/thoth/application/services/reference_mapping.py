"""Model output proposes quoted mappings; deterministic gates own calculation and persistence."""

from dataclasses import dataclass

from pydantic import ValidationError

from thoth.application.services.criterion_projection import criterion_projection
from thoth.domain.canonical import head_set_digest
from thoth.domain.criterion_contract import CriterionContractRecord
from thoth.domain.enums import ModelRole
from thoth.domain.evidence import EvidenceSpan, InformationSufficiencyAssessment
from thoth.domain.model import ContextPack, ModelRequest
from thoth.domain.reference import (
    ReferenceContext,
    ReferenceError,
    ReferenceInquiry,
    ReferenceMappingProposal,
    ReferenceMappingTrace,
)
from thoth.domain.research_execution import research_work
from thoth.ports.ledger import LedgerPort
from thoth.ports.model import ModelExecutionHold, ModelResolutionError, ModelResolverPort
from thoth.ports.project import ProjectStorePort


@dataclass(frozen=True)
class MappedReference:
    proposal: ReferenceMappingProposal
    trace: ReferenceMappingTrace


class ModelReferenceMapper:
    def __init__(
        self, models: ModelResolverPort, projects: ProjectStorePort, ledger: LedgerPort
    ) -> None:
        self._models, self._projects, self._ledger = models, projects, ledger

    async def map(
        self,
        *,
        project_id: str,
        thread_id: str,
        provider: str,
        model: str | None,
        instruction: str,
        criterion: CriterionContractRecord,
        target: ReferenceContext,
        evidence: tuple[EvidenceSpan, ...],
        assessment: InformationSufficiencyAssessment,
        prior: ReferenceInquiry | None,
    ) -> MappedReference:
        project = self._projects.read(project_id)
        if project is None:
            raise ReferenceError("REFERENCE_SCOPE_NOT_FOUND")
        heads = head_set_digest(self._ledger.read_heads(project_id))
        context = ContextPack(
            case_id=f"reference:{thread_id}",
            project_id=project_id,
            object_id=assessment.target_object_id,
            problem=instruction,
            evidence=evidence,
            criteria=(criterion_projection(criterion),),
            sufficiency=assessment,
            input_head_set_digest=heads,
            policy_hints={
                "reference_mapping": {
                    "target": target.model_dump(mode="python"),
                    "questions": () if prior is None else prior.questions,
                    "instructions": (
                        "Map measurements only to supplied span IDs and exact quotes. "
                        "Cite each field's supporting span in the measurement artifact. "
                        "Do not calculate ranges, invent values or fill target preferences. "
                        "Do not authorize anything. Answers need a listed question field "
                        "and literal quotes from the user's instruction. "
                        "Order only missing target fields in missing_field_order."
                        " Scenarios may select subsets of cited measurement spans under the "
                        "same declared target context; they must not invent observations."
                    ),
                    "output_is_candidate": True,
                }
            },
        )
        try:
            work = research_work.get()
            if work is not None:
                context = context.model_copy(update={"research_context": work.context})
            result = await self._models.resolve(provider=provider, model=model).structured(
                ModelRequest(
                    role=ModelRole.REFERENCE_MAPPER,
                    project_id=project_id,
                    cutoff_at=project.cutoff_at,
                    context_pack=context,
                    output_model=ReferenceMappingProposal,
                    prompt_version="reference.mapping.v1",
                    model_policy_ref=project.policy_binding_ref,
                    max_output_tokens=4096,
                )
            )
        except (ModelExecutionHold, ModelResolutionError):
            raise ReferenceError("REFERENCE_MAPPING_UNAVAILABLE") from None
        except ValidationError:
            raise ReferenceError("REFERENCE_MAPPING_INVALID") from None
        after = self._projects.read(project_id)
        if after != project or head_set_digest(self._ledger.read_heads(project_id)) != heads:
            raise ReferenceError("REFERENCE_MAPPING_BASIS_CHANGED")
        return MappedReference(
            result.output,
            ReferenceMappingTrace(
                model_id=result.model_id,
                prompt_version=result.prompt_version,
                input_digest=result.input_digest,
                output_digest=result.output_digest,
                input_head_set_digest=heads,
                project_revision=project.revision,
                policy_ref=project.policy_binding_ref,
                scripted=result.scripted,
            ),
        )
