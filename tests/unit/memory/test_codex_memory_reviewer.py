from __future__ import annotations

from datetime import UTC, datetime

import pytest

from thoth.adapters.memory import CodexOAuthMemoryReviewer
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.memory import MemoryReviewContext, MemoryReviewRole, MemoryReviewVerdict
from thoth.ports.model import ModelExecutionHold


class FakeExecutor:
    model_label = "codex-oauth/test-memory"

    def __init__(self, output: str) -> None:
        self.output = output
        self.prompts: list[str] = []
        self.schemas: list[dict[str, object]] = []

    async def execute(self, prompt: str, schema: dict[str, object]) -> str:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return self.output


def _context(role: MemoryReviewRole) -> MemoryReviewContext:
    draft = {
        "role": role.value,
        "candidate_digest": "a" * 64,
        "fields": {
            "unsafe": False,
            "missing": False,
            "conflict": False,
            "cutoff": datetime(2026, 9, 2, tzinfo=UTC),
        },
        "bounded": True,
    }
    return MemoryReviewContext.model_validate(
        {
            **draft,
            "context_digest": domain_digest(
                "MEMORY_REVIEW_CONTEXT", "1.0.0", canonical_payload(draft)
            ),
        }
    )


@pytest.mark.asyncio
async def test_codex_reviewer_records_model_schema_and_basis_digests() -> None:
    executor = FakeExecutor('{"verdict":"PASS","reason_code":"MODEL_CONTEXT_PASS"}')
    reviewer = CodexOAuthMemoryReviewer(executor)

    review = await reviewer.review(_context(MemoryReviewRole.FACTS))

    assert review.verdict == MemoryReviewVerdict.PASS
    assert review.model_id == executor.model_label
    assert review.prompt_version == "memory-review.facts.v1"
    assert len(review.model_input_digest or "") == 64
    assert len(review.model_output_digest or "") == 64
    assert len(review.schema_digest or "") == 64
    assert len(review.basis_digest) == 64
    assert review.scripted is False
    assert review.institutionally_independent is False
    assert "untrusted data" in executor.prompts[0]
    assert executor.schemas[0]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_codex_reviewer_invalid_schema_is_a_typed_model_hold() -> None:
    reviewer = CodexOAuthMemoryReviewer(FakeExecutor('{"verdict":"PASS"}'))

    with pytest.raises(ModelExecutionHold):
        await reviewer.review(_context(MemoryReviewRole.DREAM))
