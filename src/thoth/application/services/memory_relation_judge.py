"""A relation judge that asks the research model port, so budget and usage are recorded there.

It is not connected to the default product; memory that the rules cannot settle is held until a
model reviewer is connected on purpose. It opens no separate model route: the caller passes the
same `ModelPort` an investigation already uses.
"""

from __future__ import annotations

from datetime import datetime

from thoth.domain.enums import ModelRole
from thoth.domain.memory_relation import MemoryRelationProposal, MemoryRelationQuestion
from thoth.domain.model import ContextPack, ModelRequest
from thoth.ports.model import ModelPort

PROMPT_VERSION = "memory_relation.v1"
_TASK = (
    "Two stored project memories are shown in the given order. Say how they relate: DUPLICATE, "
    "CONTAINS, UPDATE, CONTRADICTION_CANDIDATE, UNRELATED or AMBIGUOUS. Quote, word for word, "
    "one sentence from the first memory in first_span and one from the second in second_span. "
    "If you cannot quote both, answer AMBIGUOUS. Your answer is a proposal, not a decision."
)


class ModelMemoryRelationJudge:
    def __init__(
        self,
        model: ModelPort,
        *,
        project_id: str,
        cutoff_at: datetime,
        model_policy_ref: str,
        head_set_digest: str,
        max_output_tokens: int = 600,
    ) -> None:
        self._model = model
        self._project_id, self._cutoff_at = project_id, cutoff_at
        self._policy_ref, self._heads = model_policy_ref, head_set_digest
        self._max_output_tokens = max_output_tokens

    async def judge(self, question: MemoryRelationQuestion) -> MemoryRelationProposal:
        context = ContextPack(
            case_id=f"memory-relation:{question.first.memory_id}:{question.second.memory_id}",
            project_id=self._project_id,
            object_id="memory-relation",
            problem="How do these two stored memories relate?",
            evidence=(),
            criteria=(),
            sufficiency=None,
            input_head_set_digest=self._heads,
            research_context={
                "task": _TASK,
                "first_memory": question.first.model_dump(mode="json"),
                "second_memory": question.second.model_dump(mode="json"),
            },
        )
        result = await self._model.structured(
            ModelRequest(
                role=ModelRole.MEMORY_RELATION_JUDGE,
                project_id=self._project_id,
                cutoff_at=self._cutoff_at,
                context_pack=context,
                output_model=MemoryRelationProposal,
                prompt_version=PROMPT_VERSION,
                model_policy_ref=self._policy_ref,
                max_output_tokens=self._max_output_tokens,
            )
        )
        return result.output
