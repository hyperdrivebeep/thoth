"""Simplified Chinese answers: codec 2.2.0, provider wording, and ko/en left byte-identical."""

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
    with_request_language,
)
from thoth.domain.canonical import canonical_payload
from thoth.domain.research_codec import decode_research_record
from thoth.domain.research_request import (
    ThreadRequestRevision,
    ThreadRequestRevisionV21,
    ThreadRequestRevisionV22,
    decode_thread_request,
)
from thoth.protocol.jsonrpc import RpcApplicationError

# Hashes of what the code wrote before zh-Hans existed (taken from the commit before this
# change); ko and en must keep producing exactly these.
KO_EN_REQUEST_SHA256 = {
    "ko": "3fd99cf287ba5fdb638f8d6035eca1b0c15871d0813090eecfccb294cf35deab",
    "en": "9700e4429f7c717a91894c2ffbbeb190737c64fa2ff3dd2e898860d6a28dd961",
}
KO_EN_PROVIDER_SHA256 = {
    "ko": (
        "329455779d85af8eabc4ac852e89cbebeee6a588c2d8198143d1ef7ac2ceedc6",
        "75d31a7d291ac8fc3760b94832bd1f944cbbbfb107479e0e27808b0f75e1e4d6",
        "e861ad4d407abf59ca1ef05b6da1963c906f6ca3d9e013f1c783178b4f928896",
    ),
    "en": (
        "8ac415bdd51cc91b311f8f866eb20c8f476e96a04f4acb109c1e1f18434da749",
        "9d088a2849b1c9c2ff0dbc4a001fcbdf0a87f318cda7deb0a47fb0198ed401ec",
        "d6bec9c13c51f1f8e22b8213b5fcecc295772357c065c55de2745b8cd750e124",
    ),
}


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


@pytest.mark.parametrize("language", ["ko", "en"])
def test_ko_and_en_requests_keep_codec_2_1_0_and_the_exact_bytes_written_before(language: str):
    explicit = with_request_language(request_record(request_ref()), {"response_language": language})
    assert isinstance(explicit, ThreadRequestRevisionV21)
    assert explicit.schema_version == "2.1.0"
    assert sha(canonical_payload(explicit)) == KO_EN_REQUEST_SHA256[language]
    assert type(decode_thread_request(explicit.model_dump())) is ThreadRequestRevisionV21
    assert type(decode_research_record(explicit.model_dump())) is ThreadRequestRevisionV21


def test_zh_hans_is_written_in_codec_2_2_0_and_round_trips():
    original = request_record(request_ref())
    chinese = with_request_language(original, {"response_language": "zh-Hans"})
    assert isinstance(chinese, ThreadRequestRevisionV22)
    assert chinese.schema_version == "2.2.0" and chinese.response_language == "zh-Hans"
    for decoded in (
        decode_thread_request(chinese.model_dump()),
        decode_research_record(chinese.model_dump()),
    ):
        assert type(decoded) is ThreadRequestRevisionV22
        assert canonical_payload(decoded) == canonical_payload(chinese)
    assert resume_language(chinese, {}) == {"response_language": "zh-Hans"}
    assert resume_language(chinese, {"response_language": "zh-Hans"}) == {
        "response_language": "zh-Hans"
    }
    with pytest.raises(RpcApplicationError, match="REQUEST_LANGUAGE_CONFLICT"):
        resume_language(chinese, {"response_language": "ko"})
    # The original record is untouched.
    assert "response_language" not in original.model_dump()


def test_readers_older_than_2_2_0_refuse_zh_hans_instead_of_reading_it_as_something_else():
    chinese = with_request_language(request_record(request_ref()), {"response_language": "zh-Hans"})
    with pytest.raises(ValidationError):
        ThreadRequestRevisionV21.model_validate(chinese.model_dump())
    with pytest.raises(ValidationError):
        ThreadRequestRevision.model_validate(chinese.model_dump())
    with pytest.raises(ValidationError):
        ThreadRequestRevisionV21.model_validate({**chinese.model_dump(), "schema_version": "2.1.0"})


def test_a_2_2_0_record_may_also_hold_ko_or_en_and_is_closed_to_later_languages():
    korean = with_request_language(request_record(request_ref()), {"response_language": "ko"})
    wide = {**korean.model_dump(), "schema_version": "2.2.0"}
    decoded = decode_thread_request(wide)
    assert isinstance(decoded, ThreadRequestRevisionV22) and decoded.response_language == "ko"
    with pytest.raises(ValidationError):
        decode_thread_request({**wide, "response_language": "ja"})
    with pytest.raises(RpcApplicationError, match="RESPONSE_LANGUAGE_UNSUPPORTED"):
        with_request_language(request_record(request_ref()), {"response_language": "fr"})


@pytest.mark.parametrize("language", ["ko", "en"])
def test_ko_and_en_provider_wording_is_byte_identical_to_before(language: str):
    request = model_request_language(model_request(), language)
    assert (
        sha(user_visible_language_contract(request)),
        sha(prompt_envelope(request)),
        sha(OpenAIResponsesModel.instructions(request)),
    ) == KO_EN_PROVIDER_SHA256[language]


def test_zh_hans_gets_the_chinese_guidance_and_labels_and_not_the_english_or_korean_ones():
    chinese = user_visible_language_contract(model_request_language(model_request(), "zh-Hans"))
    english = user_visible_language_contract(model_request_language(model_request(), "en"))
    korean = user_visible_language_contract(model_request_language(model_request(), "ko"))
    assert "response language to Simplified Chinese" in chinese
    assert "Simplified Chinese characters, not Traditional" in chinese
    assert "easy Simplified Chinese" in chinese
    assert "'事实' and '未知'" in chinese
    # The language-neutral plain-writing rules are shared with the other languages.
    for rule in ("short plain sentences", "program-internal codes, field names"):
        assert rule in chinese
    # None of the English or Korean wording leaks in, and there is no silent English fallback.
    assert "response language to English" not in chinese
    assert "'Fact' and 'Unknown'" not in chinese
    assert "Use plain English for internal terminology" not in chinese
    assert "detection rate at least 0.90" not in chinese
    assert "'사실'" not in chinese and "'확인 불가'" not in chinese
    assert "write easy Korean" not in chinese
    # And the other two did not pick up the Chinese wording.
    assert "事实" not in english and "事实" not in korean
    assert "Simplified Chinese" not in english and "Simplified Chinese" not in korean


def test_zh_hans_reaches_the_provider_payloads_without_the_korean_only_rule():
    request = model_request_language(model_request(), "zh-Hans")
    for payload in (prompt_envelope(request), OpenAIResponsesModel.instructions(request)):
        assert "response language to Simplified Chinese" in payload
        assert "for a Korean question write them in Hangul" not in payload
        assert "response language to English" not in payload


def test_the_partial_draft_prefix_follows_the_language_and_defaults_to_the_legacy_korean():
    assert partial_answer("a", None).startswith("검증 보류")
    assert partial_answer("a", "ko").startswith("검증 보류")
    assert partial_answer("a", "en").startswith("Review on hold")
    assert partial_answer("a", "zh-Hans").startswith("复核暂缓")
    with pytest.raises(ValueError, match="RESPONSE_LANGUAGE_UNSUPPORTED"):
        partial_answer("a", "fr")
