"""Which answer language a question is written in, read from its scripts alone.

An explicit choice always wins. Detection is only a hint for a request that made no choice, and it
answers None whenever the script cannot say: text with no letters is never guessed as one of the
supported languages. Kana means Japanese, unless Hangul is present. Han text with more
Traditional-only than Simplified-only characters is Traditional Chinese.

Limits, stated so nobody trusts it too far: Han characters alone cannot tell Simplified from
Traditional Chinese or from Japanese written only in kanji, so a kanji-only text follows the
Simplified Chinese rule. The Traditional-only and Simplified-only character lists below are a
small set of very common characters, not a classifier: on a synthetic sample of twelve-character
Traditional Chinese strings about half contain none of them and are read as Simplified Chinese.
Nothing calls this detection yet, so that limit matters only once an automatic mode does.
"""

import re

from thoth.domain.response_language import ResponseLanguage, explicit_language

_HANGUL = re.compile("[가-힣ㄱ-ㅎㅏ-ㅣ]")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
# Kana letters, half-width katakana included; the katakana middle dot (U+30FB) is punctuation and
# is left out.
_KANA = re.compile(r"[\u3040-\u30fa\u30fc-\u30ff\uff66-\uff9f]")
_LATIN = re.compile("[A-Za-z]")
# One Han character carries about as much as three or four Latin letters.
_LATIN_PER_HAN = 4
_TRADITIONAL_ONLY = frozenset("們個這來對時學國會後發體說與為從樣機點開關資軟間問題")
_SIMPLIFIED_ONLY = frozenset("们个这来对时学国会后发体说与为从样机点开关资软间问题")


def detect_question_language(text: str) -> ResponseLanguage | None:
    """ko, ja, en, zh-Hant or zh-Hans when the scripts say so; None when they do not."""
    if _HANGUL.search(text):
        return "ko"  # Hanja and quoted kana inside Korean text do not change the language
    if _KANA.search(text):
        return "ja"  # kana is Japanese, never read as Chinese
    han = len(_HAN.findall(text))
    latin = len(_LATIN.findall(text))
    if han >= 2 and han * _LATIN_PER_HAN >= latin:
        traditional = sum(char in _TRADITIONAL_ONLY for char in text)
        simplified = sum(char in _SIMPLIFIED_ONLY for char in text)
        return "zh-Hant" if traditional > simplified else "zh-Hans"
    return "en" if latin else None


def resolve_response_language(explicit: object, question: str) -> ResponseLanguage | None:
    """The explicit choice when there is one (an unsupported value is an error), else detection."""
    return detect_question_language(question) if explicit is None else explicit_language(explicit)
