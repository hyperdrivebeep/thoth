"""The one model call an investigation makes to widen its question for memory recall.

The caller awaits this before build_context and hands the outcome over. It is asked at most once
per investigation: a second caller in the same attempt gets the first answer. A failure, a time-out
or a reply in the wrong shape never fails the investigation; recall then runs on the question's
own words and the selection record says why.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from pydantic import ValidationError

from thoth.application.services.full_project_memory import FullProjectMemoryService
from thoth.domain.enums import ModelRole
from thoth.domain.memory_expansion import (
    MEMORY_QUERY_EXPANSION_PURPOSE,
    MemoryExpansionOutcome,
    MemoryExpansionRecord,
    QueryExpansion,
)
from thoth.domain.memory_terms import added_words, words
from thoth.domain.model import ContextPack, ModelRequest
from thoth.domain.research_execution import ResearchFence, ResearchWork, model_purpose
from thoth.domain.research_lease import ResearchLeaseLost, ResearchPaused
from thoth.ports.memory import MemoryQueryExpanderPort
from thoth.ports.model import ModelOutputContractHold, ModelPort

PROMPT_VERSION = "memory_query_expansion.v2"
EXPANSION_MAX_OUTPUT_TOKENS = 300
EXPANSION_TIMEOUT_SECONDS = 20.0
CACHE_KEY = "memory_query_expansion"
_TASK = (
    "A user asks a question about a project. Stored project memories are short notes, written in "
    "their own words, that may be about the same thing in other words. Return search words only: "
    "synonyms (other words for what the question says), keywords (the topic in another language, "
    "such as English for a Korean question and the reverse), related (closely related terms) and "
    "note_line (one sentence written the way a stored memory about this question might read). "
    "At most eight short phrases in each list. Do not answer the question. Your words only help "
    "search; they decide nothing."
)


class ModelMemoryQueryExpander:
    """Asks the same ModelPort the investigation already uses; opens no separate model route."""

    def __init__(
        self,
        model: ModelPort,
        *,
        project_id: str,
        cutoff_at: datetime,
        model_policy_ref: str,
        head_set_digest: str,
        max_output_tokens: int = EXPANSION_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._model = model
        self._project_id, self._cutoff_at = project_id, cutoff_at
        self._policy_ref, self._heads = model_policy_ref, head_set_digest
        self._max_output_tokens = max_output_tokens
        self.dispatch_ids: tuple[str, ...] = ()

    async def expand(self, query: str) -> QueryExpansion:
        context = ContextPack(
            case_id=f"memory-expansion:{self._project_id}",
            project_id=self._project_id,
            object_id="memory-expansion",
            problem=query,
            evidence=(),
            criteria=(),
            sufficiency=None,
            input_head_set_digest=self._heads,
            research_context={"task": _TASK},
        )
        token = model_purpose.set(MEMORY_QUERY_EXPANSION_PURPOSE)
        try:
            result = await self._model.structured(
                ModelRequest(
                    role=ModelRole.MEMORY_QUERY_EXPANDER,
                    project_id=self._project_id,
                    cutoff_at=self._cutoff_at,
                    context_pack=context,
                    output_model=QueryExpansion,
                    prompt_version=PROMPT_VERSION,
                    model_policy_ref=self._policy_ref,
                    max_output_tokens=self._max_output_tokens,
                )
            )
        finally:
            model_purpose.reset(token)
        self.dispatch_ids = result.dispatch_ids
        return result.output


async def widen_query(
    *,
    memory: FullProjectMemoryService,
    expander: MemoryQueryExpanderPort,
    work: ResearchWork | None,
    project_id: str,
    query: str,
    timeout_seconds: float = EXPANSION_TIMEOUT_SECONDS,
) -> MemoryExpansionOutcome:
    """The widened question for this investigation: asked once, remembered for the next caller."""

    cache = None if work is None else work.preprocessing_cache
    cached = None if cache is None else cache.get(CACHE_KEY)
    if isinstance(cached, MemoryExpansionOutcome):
        return cached
    outcome = await _ask(memory, expander, project_id, query, timeout_seconds)
    if cache is not None:
        cache[CACHE_KEY] = outcome
    return outcome


async def _ask(
    memory: FullProjectMemoryService,
    expander: MemoryQueryExpanderPort,
    project_id: str,
    query: str,
    timeout_seconds: float,
) -> MemoryExpansionOutcome:
    skipped = memory.expansion_skip_reason(project_id, query)
    if skipped is not None:
        return MemoryExpansionOutcome(record=MemoryExpansionRecord(reason=skipped))

    def failed(status: str, reason: str) -> MemoryExpansionOutcome:
        record = MemoryExpansionRecord.model_validate(
            {"status": status, "reason": reason, "dispatch_ids": dispatch_ids()}
        )
        return MemoryExpansionOutcome(record=record)

    def dispatch_ids() -> tuple[str, ...]:
        return expander.dispatch_ids if isinstance(expander, ModelMemoryQueryExpander) else ()

    try:
        expansion = await asyncio.wait_for(expander.expand(query), timeout_seconds)
    except (ResearchFence, ResearchLeaseLost, ResearchPaused):
        raise
    except (ModelOutputContractHold, ValidationError) as exc:
        return failed("EXPANSION_INVALID", type(exc).__name__)
    except TimeoutError:
        return failed("EXPANSION_FAILED", "TIMEOUT")
    except Exception as exc:
        return failed("EXPANSION_FAILED", type(exc).__name__)
    added = added_words(expansion.text(), words(query))
    record = MemoryExpansionRecord(
        status="USED",
        added_words=tuple(word.run for word in added),
        dispatch_ids=dispatch_ids(),
    )
    return MemoryExpansionOutcome(record=record, expansion=expansion)
