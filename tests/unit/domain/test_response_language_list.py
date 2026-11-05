"""The one list of answer languages, and everything keyed by it, stays complete."""

from typing import get_args

import pytest

from thoth.domain.research_request import LANGUAGE_REQUEST_CODECS
from thoth.domain.response_language import (
    SUPPORTED_RESPONSE_LANGUAGES,
    LegacyResponseLanguage,
    ResponseLanguage,
    explicit_language,
    language_instruction,
    request_codec_version,
)


def test_the_list_is_the_literal_and_names_exactly_the_decided_languages():
    assert get_args(ResponseLanguage) == SUPPORTED_RESPONSE_LANGUAGES
    assert SUPPORTED_RESPONSE_LANGUAGES == ("ko", "en", "zh-Hans")
    assert set(get_args(LegacyResponseLanguage)) <= set(SUPPORTED_RESPONSE_LANGUAGES)


@pytest.mark.parametrize("language", SUPPORTED_RESPONSE_LANGUAGES)
def test_every_language_has_a_name_a_codec_and_an_accepted_value(language: ResponseLanguage):
    assert explicit_language(language) == language
    assert request_codec_version(language) in LANGUAGE_REQUEST_CODECS
    assert "response language to" in language_instruction(language)


@pytest.mark.parametrize("value", ["ja", "zh", "zh-Hant", "EN", "", None, 1])
def test_anything_outside_the_list_is_refused_not_defaulted(value: object):
    with pytest.raises(ValueError, match="RESPONSE_LANGUAGE_UNSUPPORTED"):
        explicit_language(value)


def test_ko_and_en_keep_the_oldest_codec_so_their_bytes_never_change():
    assert {request_codec_version("ko"), request_codec_version("en")} == {"2.1.0"}
    assert request_codec_version("zh-Hans") == "2.2.0"
