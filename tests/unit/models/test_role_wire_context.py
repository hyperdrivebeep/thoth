import hashlib
import json
from datetime import UTC, datetime

from thoth.adapters.models.codex_oauth import (
    _prompt_envelope,  # pyright: ignore[reportPrivateUsage]
    role_contract,
)
from thoth.domain.artifact import SourceLocator
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    ModelRole,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.evidence_requirements import EvidenceRanking
from thoth.domain.model import ContextPack, ModelRequest


def test_shared_metadata_compression_preserves_text_locator_authority_and_cutoff() -> None:
    spans = tuple(
        EvidenceSpan(
            span_id=f"span:{i}",
            project_id="p",
            artifact_id="a",
            source_version_id="v",
            locator=SourceLocator(page=2, line=i + 1),
            exact_text=f"Independent contrary condition {i}",
            text_sha256=hashlib.sha256(f"Independent contrary condition {i}".encode()).hexdigest(),
            extraction_method="fixture:1",
            support_state=SupportState.EXTRACTED,
            authority_state=AuthorityState.INFORMAL,
            verification_state=VerificationState.SCHEMA_VALID,
            cutoff_state=CutoffState.ELIGIBLE,
        )
        for i in range(30)
    )
    context = ContextPack(
        case_id="c",
        project_id="p",
        object_id="o",
        problem="compare conditions",
        evidence=spans,
        criteria=(),
        sufficiency=None,
        input_head_set_digest="a" * 64,
    )
    request = ModelRequest(
        role=ModelRole.EVIDENCE_RERANKER,
        project_id="p",
        cutoff_at=datetime.now(UTC),
        context_pack=context,
        output_model=EvidenceRanking,
        prompt_version="fixture",
        model_policy_ref="policy",
        max_output_tokens=1000,
    )
    text = _prompt_envelope(request)
    section = text.split("UNTRUSTED_EVIDENCE_SPANS\n")[1].split("\n\nTASK\n")[0]
    wire = json.loads(section)
    original: list[dict[str, object]] = []
    for span, row in zip(spans, wire["spans"], strict=True):
        source = wire["sources"][row["source_index"]]
        assert row["exact_text"] == span.exact_text and row["text_sha256"] == span.text_sha256
        assert SourceLocator.model_validate(row["locator"]) == span.locator
        assert (
            source["authority_state"] == span.authority_state.value
            and source["cutoff_state"] == span.cutoff_state.value
        )
        assert source["source_version_id"] == span.source_version_id
        original.append(
            {
                **source,
                "span_id": span.span_id,
                "text_sha256": span.text_sha256,
                "locator": span.locator,
                "exact_text": span.exact_text,
            }
        )
    assert len(wire["sources"]) == 1
    assert len(section.encode()) < len(canonical_payload({"spans": original}))


def test_user_facing_prose_follows_question_language_without_translating_identifiers() -> None:
    prompt = _prompt_envelope(
        ModelRequest(
            role=ModelRole.SEMANTIC_REVIEWER,
            project_id="p",
            cutoff_at=datetime.now(UTC),
            context_pack=ContextPack(
                case_id="c",
                project_id="p",
                object_id="o",
                problem="한국어 질문",
                evidence=(),
                criteria=(),
                sufficiency=None,
                input_head_set_digest="a" * 64,
            ),
            output_model=EvidenceRanking,
            prompt_version="fixture",
            model_policy_ref="policy",
            max_output_tokens=1000,
        )
    )
    assert "same language as context_pack.problem" in prompt
    assert "do not switch that prose to English" in prompt
    assert "FACT/UNKNOWN" in prompt
    assert "answer and explanation prose" in role_contract("SEMANTIC_REVIEWER")
    assert "statement, uncertainty, counterevidence_queries" in role_contract(
        "HYPOTHESIS_GENERATOR"
    )
    assert "specification, expected_information_value" in role_contract("ACTION_PLANNER")


def test_user_facing_prose_is_asked_to_be_plain_korean_without_internal_names() -> None:
    prompt = _prompt_envelope(
        ModelRequest(
            role=ModelRole.SEMANTIC_REVIEWER,
            project_id="p",
            cutoff_at=datetime.now(UTC),
            context_pack=ContextPack(
                case_id="c",
                project_id="p",
                object_id="o",
                problem="한국어 질문",
                evidence=(),
                criteria=(),
                sufficiency=None,
                input_head_set_digest="a" * 64,
            ),
            output_model=EvidenceRanking,
            prompt_version="fixture",
            model_policy_ref="policy",
            max_output_tokens=1000,
        )
    )
    assert "easy Korean" in prompt
    # The reader is a radar R&D engineer: standard terms and units stay as the material has them.
    assert "standard abbreviations" in prompt and "SNR" in prompt and "dB" in prompt
    assert "not an engineer" not in prompt and "English abbreviations" not in prompt
    assert "internal codes" in prompt and "field names" in prompt and "made-up ids" in prompt
    assert "탐지율 0.90 이상" in prompt  # a criterion is spelled out in words
    assert "사실" in prompt and "확인 불가" in prompt  # the two labels are written in Korean
    # Names that must reach the reader unchanged are still protected from translation.
    assert "span IDs, file names" in prompt and "quoted source text" in prompt
    # The old rule that kept the labels in English is gone; the new one names them once.
    assert "FACT/UNKNOWN labels, GPU" not in prompt
    assert "quoted evidence, FACT/UNKNOWN labels" not in prompt
    # The structured output rules are untouched.
    assert "Return only the schema object." in prompt and "Do not invent source IDs." in prompt
