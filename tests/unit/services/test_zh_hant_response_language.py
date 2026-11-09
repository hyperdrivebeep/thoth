"""Traditional Chinese answers: codec 2.4.0, wording, and earlier languages left byte-identical."""

import hashlib
import json
from pathlib import Path

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
    ThreadRequestRevisionV24,
    decode_thread_request,
)
from thoth.protocol.jsonrpc import RpcApplicationError

ROOT = Path(__file__).resolve().parents[3]
# Hashes of what the code wrote before Traditional Chinese existed (the commit before this change).
REQUEST_SHA256 = {
    "ko": "3fd99cf287ba5fdb638f8d6035eca1b0c15871d0813090eecfccb294cf35deab",
    "en": "9700e4429f7c717a91894c2ffbbeb190737c64fa2ff3dd2e898860d6a28dd961",
    "zh-Hans": "746376a2ebad307692831466956c34d09bcc79be774771a093b72997b9ba52f6",
    "ja": "8c38d300a7cfb684e2dcc23e87d85698ff5af43849e0845d19f49187b38f7c28",
}
PROVIDER_SHA256 = {
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
    "zh-Hans": (
        "b4282d53a258647e1bb4972a5c7a786608772ecd84d94f0a33f28a541ca27550",
        "b421c5cbc49d268699e7a427171f245c5febac1e5458c8a5e40e4f828535c637",
        "83eeaba7cd670bf7570e2897289c3c6d9d0515b8853f6d3d8f68022f73164f1f",
    ),
    "ja": (
        "27d7384c70d6e8bde1979c94cbee011ffd579cdd6c5042841fb7d5b9e341109a",
        "1d85b8c5994b47bbda790e8ac340f653628c4458647687bf1778f580f9d01184",
        "3f6e43e9e5d3247fc634897f92e4578062e325a02ad55a9015dc680da7e1b121",
    ),
}
SCHEMA_SHA256 = {
    "v21": "04e487974f47bb1d3178cc8663e70224f7bbe5593b1c22a38fde7576c6c23652",
    "v22": "d3ed19e7361667496122c9de7a0466f7fae05992df12b46644583fee1b6e512e",
    "v23": "9c7360d66a7638fb2494af2143f042171327bd863d87a2559f603da7c819079d",
}


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def request_in(language: str) -> ThreadRequestRevision:
    return with_request_language(request_record(request_ref()), {"response_language": language})


def contract(language: str) -> str:
    return user_visible_language_contract(model_request_language(model_request(), language))


def test_zh_hant_is_written_in_codec_2_4_0_and_round_trips():
    original = request_record(request_ref())
    traditional = with_request_language(original, {"response_language": "zh-Hant"})
    assert isinstance(traditional, ThreadRequestRevisionV24)
    assert traditional.schema_version == "2.4.0" and traditional.response_language == "zh-Hant"
    for decoded in (
        decode_thread_request(traditional.model_dump()),
        decode_research_record(traditional.model_dump()),
    ):
        assert type(decoded) is ThreadRequestRevisionV24
        assert canonical_payload(decoded) == canonical_payload(traditional)
    assert resume_language(traditional, {}) == {"response_language": "zh-Hant"}
    with pytest.raises(RpcApplicationError, match="REQUEST_LANGUAGE_CONFLICT"):
        resume_language(traditional, {"response_language": "zh-Hans"})
    assert "response_language" not in original.model_dump()


@pytest.mark.parametrize(
    ("language", "codec", "version"),
    [
        ("ko", ThreadRequestRevisionV21, "2.1.0"),
        ("en", ThreadRequestRevisionV21, "2.1.0"),
        ("zh-Hans", ThreadRequestRevisionV22, "2.2.0"),
        ("ja", ThreadRequestRevisionV23, "2.3.0"),
    ],
)
def test_every_earlier_language_keeps_its_codec_and_the_exact_bytes_written_before(
    language: str, codec: type[ThreadRequestRevision], version: str
):
    record = request_in(language)
    assert type(record) is codec and record.schema_version == version
    assert sha(canonical_payload(record)) == REQUEST_SHA256[language]
    assert type(decode_thread_request(record.model_dump())) is codec
    assert type(decode_research_record(record.model_dump())) is codec


def test_readers_older_than_2_4_0_refuse_zh_hant_instead_of_reading_it_as_something_else():
    data = request_in("zh-Hant").model_dump()
    for codec, version in (
        (ThreadRequestRevisionV23, "2.3.0"),
        (ThreadRequestRevisionV22, "2.2.0"),
        (ThreadRequestRevisionV21, "2.1.0"),
    ):
        with pytest.raises(ValidationError):
            codec.model_validate({**data, "schema_version": version})
        with pytest.raises(ValidationError):
            decode_thread_request({**data, "schema_version": version})
    with pytest.raises(ValidationError):
        ThreadRequestRevisionV23.model_validate(data)
    with pytest.raises(ValidationError):
        ThreadRequestRevision.model_validate(data)


def test_a_2_4_0_record_may_also_hold_every_earlier_language_and_an_unknown_one_is_refused():
    for language in ("ko", "en", "zh-Hans", "ja"):
        wide = {**request_in(language).model_dump(), "schema_version": "2.4.0"}
        decoded = decode_thread_request(wide)
        assert isinstance(decoded, ThreadRequestRevisionV24)
        assert decoded.response_language == language
    with pytest.raises(ValidationError):
        decode_thread_request({**wide, "response_language": "fr"})
    with pytest.raises(RpcApplicationError, match="RESPONSE_LANGUAGE_UNSUPPORTED"):
        with_request_language(request_record(request_ref()), {"response_language": "fr"})


def test_the_published_schemas_of_codecs_2_1_to_2_3_do_not_change():
    for name, expected in SCHEMA_SHA256.items():
        path = ROOT / "schemas" / "protocol" / f"thread-request-{name}.schema.json"
        assert sha(path.read_bytes()) == expected, name
    languages = {
        name: json.loads(
            (ROOT / "schemas" / "protocol" / f"thread-request-{name}.schema.json").read_text(
                encoding="utf-8"
            )
        )["properties"]["response_language"]["enum"]
        for name in ("v23", "v24")
    }
    assert languages["v23"] == ["ko", "en", "zh-Hans", "ja"]
    assert languages["v24"] == ["ko", "en", "zh-Hans", "ja", "zh-Hant"]


@pytest.mark.parametrize("language", ["ko", "en", "zh-Hans", "ja"])
def test_provider_wording_of_every_earlier_language_is_byte_identical_to_before(language: str):
    request = model_request_language(model_request(), language)
    assert (
        sha(user_visible_language_contract(request)),
        sha(prompt_envelope(request)),
        sha(OpenAIResponsesModel.instructions(request)),
    ) == PROVIDER_SHA256[language]


def test_zh_hant_gets_the_traditional_guidance_and_labels_and_no_other_language_s():
    traditional = contract("zh-Hant")
    assert "response language to Traditional Chinese" in traditional
    assert "Use Traditional Chinese characters, not Simplified Chinese characters" in traditional
    assert "easy Traditional Chinese" in traditional
    assert "'事實' and '未知'" in traditional and "'檢測率不低於 0.90'" in traditional
    for rule in ("short plain sentences", "program-internal codes, field names"):
        assert rule in traditional
    assert "response language to English" not in traditional
    assert "to Simplified Chinese" not in traditional and "'事实'" not in traditional
    assert "Japanese" not in traditional and "'不明'" not in traditional
    assert "'Fact' and 'Unknown'" not in traditional and "'사실'" not in traditional
    for other in ("ko", "en", "zh-Hans", "ja"):
        assert "response language to Traditional" not in contract(other)
        assert "事實" not in contract(other)


def test_zh_hant_reaches_the_provider_payloads_without_the_korean_only_rule():
    request = model_request_language(model_request(), "zh-Hant")
    for payload in (prompt_envelope(request), OpenAIResponsesModel.instructions(request)):
        assert "response language to Traditional Chinese" in payload
        assert "for a Korean question write them in Hangul" not in payload
        assert "response language to English" not in payload


def test_the_partial_draft_prefix_and_the_script_checks_for_zh_hant():
    assert partial_answer("a", "zh-Hant").startswith("複核暫緩 · 部分草稿")
    assert script_check_problem("zh-Hant", "한국어 질문") == ""
    assert script_check_problem("zh-Hans", "한국어 질문") == ""
    assert script_check_problem("ko", "질문").startswith("한국어 ")
