"""Japanese answers: codec 2.3.0, provider wording, and ko/en/zh-Hans left byte-identical."""

import hashlib

import pytest
from pydantic import ValidationError
from tests.unit.services.test_request_language import model_request
from tests.unit.services.test_research_followup_contract import request_record, request_ref

from thoth.adapters.models.codex_oauth import prompt_envelope
from thoth.adapters.models.language_contract_text import user_visible_language_contract
from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.application.services.request_language import (
    model_request_language,
    partial_answer,
    resume_language,
    script_check_problem,
    with_request_language,
)
from thoth.domain.canonical import canonical_payload
from thoth.domain.research_codec import decode_research_record
from thoth.domain.research_request import (
    ThreadRequestRevision,
    ThreadRequestRevisionV21,
    ThreadRequestRevisionV22,
    ThreadRequestRevisionV23,
    decode_thread_request,
)
from thoth.protocol.jsonrpc import RpcApplicationError

# Hashes of what the code wrote before Japanese existed (the commit before this change).
ZH_REQUEST_SHA256 = "746376a2ebad307692831466956c34d09bcc79be774771a093b72997b9ba52f6"
ZH_PROVIDER_SHA256 = (
    "b4282d53a258647e1bb4972a5c7a786608772ecd84d94f0a33f28a541ca27550",
    "b421c5cbc49d268699e7a427171f245c5febac1e5458c8a5e40e4f828535c637",
    "83eeaba7cd670bf7570e2897289c3c6d9d0515b8853f6d3d8f68022f73164f1f",
)


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def test_ja_is_written_in_codec_2_3_0_and_round_trips():
    original = request_record(request_ref())
    japanese = with_request_language(original, {"response_language": "ja"})
    assert isinstance(japanese, ThreadRequestRevisionV23)
    assert japanese.schema_version == "2.3.0" and japanese.response_language == "ja"
    for decoded in (
        decode_thread_request(japanese.model_dump()),
        decode_research_record(japanese.model_dump()),
    ):
        assert type(decoded) is ThreadRequestRevisionV23
        assert canonical_payload(decoded) == canonical_payload(japanese)
    assert resume_language(japanese, {}) == {"response_language": "ja"}
    assert resume_language(japanese, {"response_language": "ja"}) == {"response_language": "ja"}
    with pytest.raises(RpcApplicationError, match="REQUEST_LANGUAGE_CONFLICT"):
        resume_language(japanese, {"response_language": "zh-Hans"})
    assert "response_language" not in original.model_dump()


def test_zh_hans_requests_keep_codec_2_2_0_and_the_exact_bytes_written_before():
    chinese = with_request_language(request_record(request_ref()), {"response_language": "zh-Hans"})
    assert isinstance(chinese, ThreadRequestRevisionV22) and chinese.schema_version == "2.2.0"
    assert sha(canonical_payload(chinese)) == ZH_REQUEST_SHA256
    assert type(decode_thread_request(chinese.model_dump())) is ThreadRequestRevisionV22
    assert type(decode_research_record(chinese.model_dump())) is ThreadRequestRevisionV22


def test_readers_older_than_2_3_0_refuse_ja_instead_of_reading_it_as_something_else():
    japanese = with_request_language(request_record(request_ref()), {"response_language": "ja"})
    data = japanese.model_dump()
    with pytest.raises(ValidationError):
        ThreadRequestRevisionV22.model_validate({**data, "schema_version": "2.2.0"})
    with pytest.raises(ValidationError):
        ThreadRequestRevisionV21.model_validate({**data, "schema_version": "2.1.0"})
    with pytest.raises(ValidationError):
        ThreadRequestRevisionV22.model_validate(data)
    with pytest.raises(ValidationError):
        ThreadRequestRevision.model_validate(data)
    with pytest.raises(ValidationError):
        decode_thread_request({**data, "schema_version": "2.2.0"})


def test_a_2_3_0_record_may_also_hold_every_earlier_language_and_an_unknown_one_is_refused():
    for language in ("ko", "en", "zh-Hans"):
        record = with_request_language(
            request_record(request_ref()), {"response_language": language}
        )
        wide = {**record.model_dump(), "schema_version": "2.3.0"}
        decoded = decode_thread_request(wide)
        assert isinstance(decoded, ThreadRequestRevisionV23)
        assert decoded.response_language == language
    with pytest.raises(ValidationError):
        decode_thread_request({**wide, "response_language": "fr"})
    with pytest.raises(RpcApplicationError, match="RESPONSE_LANGUAGE_UNSUPPORTED"):
        with_request_language(request_record(request_ref()), {"response_language": "fr"})


def test_zh_hans_provider_wording_is_byte_identical_to_before():
    request = model_request_language(model_request(), "zh-Hans")
    assert (
        sha(user_visible_language_contract(request)),
        sha(prompt_envelope(request)),
        sha(OpenAIResponsesModel.instructions(request)),
    ) == ZH_PROVIDER_SHA256


def contract(language: str) -> str:
    return user_visible_language_contract(model_request_language(model_request(), language))


def test_ja_gets_the_japanese_guidance_and_labels_and_no_other_language_s():
    japanese = contract("ja")
    assert "response language to Japanese" in japanese
    assert "Write in Japanese (kanji and kana), not in Chinese" in japanese
    assert "easy Japanese" in japanese
    assert "'事実' and '不明'" in japanese
    for rule in ("short plain sentences", "program-internal codes, field names"):
        assert rule in japanese
    assert "response language to English" not in japanese
    assert "Simplified Chinese" not in japanese and "'事实'" not in japanese
    assert "'Fact' and 'Unknown'" not in japanese
    assert "Use plain English for internal terminology" not in japanese
    assert "'사실'" not in japanese and "write easy Korean" not in japanese
    for other in ("ko", "en", "zh-Hans"):
        assert "Japanese" not in contract(other) and "事実" not in contract(other)


def test_ja_reaches_the_provider_payloads_without_the_korean_only_rule():
    request = model_request_language(model_request(), "ja")
    for payload in (prompt_envelope(request), OpenAIResponsesModel.instructions(request)):
        assert "response language to Japanese" in payload
        assert "for a Korean question write them in Hangul" not in payload
        assert "response language to English" not in payload


def test_the_partial_draft_prefix_and_the_script_checks_for_ja():
    assert partial_answer("a", "ja").startswith("検証保留")
    assert script_check_problem("ja", "한국어 질문") == ""
    assert script_check_problem("ko", "질문").startswith("한국어 ")
