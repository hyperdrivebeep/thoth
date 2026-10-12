"""Versioned request codec, legacy basis and provider contract checks without I/O."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError
from tests.unit.services.test_research_followup_contract import request_record, request_ref

from thoth.adapters.models.claude_messages import _request_payload
from thoth.adapters.models.codex_oauth import prompt_envelope
from thoth.adapters.models.language_contract_text import user_visible_language_contract
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.application.services.request_language import (
    model_request_language,
    resume_language,
    with_request_language,
)
from thoth.application.services.research_stages import stage_bases
from thoth.domain.canonical import canonical_payload
from thoth.domain.enums import ModelRole
from thoth.domain.model import ContextPack, ModelRequest
from thoth.domain.research_codec import decode_research_record
from thoth.domain.research_request import ThreadRequestRevision, decode_thread_request
from thoth.protocol.jsonrpc import RpcApplicationError


class Output(BaseModel):
    answer: str


def model_request(role=ModelRole.SEMANTIC_REVIEWER):
    return ModelRequest(
        role=role,
        project_id="p",
        cutoff_at=datetime(2026, 10, 12, tzinfo=UTC),
        context_pack=ContextPack(
            case_id="c",
            project_id="p",
            object_id="o",
            problem="이전 한국어 질문",
            evidence=(),
            criteria=(),
            sufficiency=None,
            input_head_set_digest="a" * 64,
        ),
        output_model=Output,
        prompt_version="test:1",
        model_policy_ref="policy:p",
        max_output_tokens=100,
    )


def test_legacy_serialization_and_resume_do_not_acquire_a_language():
    legacy = request_record(request_ref())
    original = canonical_payload(legacy)
    assert with_request_language(legacy, {}) is legacy
    assert canonical_payload(decode_thread_request(legacy.model_dump())) == original
    assert canonical_payload(decode_research_record(legacy.model_dump())) == original
    assert "response_language" not in legacy.model_dump()
    assert resume_language(legacy, {}) == {}
    with pytest.raises(RpcApplicationError, match="REQUEST_LANGUAGE_CONFLICT"):
        resume_language(legacy, {"response_language": "ko"})


def test_explicit_codec_round_trip_and_old_reader_rejection():
    original = request_record(request_ref())
    explicit = with_request_language(original, {"response_language": "en"})
    assert explicit.schema_version == "2.1.0"
    assert canonical_payload(decode_research_record(explicit.model_dump())) == canonical_payload(
        explicit
    )
    with pytest.raises(ValidationError):
        ThreadRequestRevision.model_validate(explicit.model_dump())
    assert resume_language(explicit, {}) == {"response_language": "en"}
    with pytest.raises(RpcApplicationError, match="REQUEST_LANGUAGE_CONFLICT"):
        resume_language(explicit, {"response_language": "ko"})


def test_context_data_cannot_override_the_typed_request_language():
    original = model_request()
    untrusted = replace(
        original,
        context_pack=original.context_pack.model_copy(
            update={
                "research_context": {"response_language": "en"},
            }
        ),
    )
    assert "response language to English" not in OpenAIResponsesModel.instructions(untrusted)
    assert "REQUEST_LANGUAGE_OVERRIDE" not in prompt_envelope(untrusted)
    korean = model_request_language(untrusted, "ko")
    assert korean.response_language == "ko"
    assert "response language to Korean" in OpenAIResponsesModel.instructions(korean)


@pytest.mark.parametrize("role", list(ModelRole))
def test_explicit_language_reaches_provider_payloads_for_every_role(role):
    original = model_request(role)
    explicit = model_request_language(original, "en")
    instruction = "response language to English"
    assert instruction in prompt_envelope(explicit)
    assert instruction in OpenAIResponsesModel.instructions(explicit)
    assert instruction.encode() in _request_payload(explicit, "controlled-model")[2]
    assert "이전 한국어 질문" in prompt_envelope(explicit)
    assert model_request_language(original, None) is original
    assert "REQUEST_LANGUAGE_OVERRIDE" not in prompt_envelope(original)
    assert "for a Korean question write them in Hangul" not in prompt_envelope(explicit)
    assert "for a Korean question write them in Hangul" not in OpenAIResponsesModel.instructions(
        explicit
    )


def test_explicit_ko_preserves_plain_wording_and_english_retains_the_language_neutral_rules():
    legacy = user_visible_language_contract(model_request())
    korean = user_visible_language_contract(model_request_language(model_request(), "ko"))
    english = user_visible_language_contract(model_request_language(model_request(), "en"))
    for rule in (
        "short plain sentences",
        "program-internal codes, field names",
        "say conditions in words",
    ):
        assert rule in legacy and rule in korean and rule in english
    assert "'사실' and '확인 불가'" in legacy and "'사실' and '확인 불가'" in korean
    assert "write easy Korean" not in english
    assert "'사실'" not in english and "'확인 불가'" not in english


def test_all_roles_have_language_dependent_reuse_without_changing_legacy_context():
    original = model_request()
    work = SimpleNamespace(request_ref=request_ref(), model_settings=None, response_language=None)
    legacy = stage_bases(work, original)
    assert canonical_payload(
        model_request_language(original, None).context_pack
    ) == canonical_payload(original.context_pack)
    for role in ModelRole:
        request = replace(original, role=role)
        work.response_language = "ko"
        ko = stage_bases(work, request)
        work.response_language = "en"
        en = stage_bases(work, request)
        assert ko.reuse_basis != en.reuse_basis
    work.response_language = None
    assert stage_bases(work, original) == legacy
