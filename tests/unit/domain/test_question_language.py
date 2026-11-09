"""Question-language detection reads scripts only and never guesses what it cannot tell."""

import pytest

from thoth.domain.question_language import detect_question_language, resolve_response_language


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # Korean, with and without Hanja or a quoted foreign word: Hangul decides.
        ("레이더 탐지율이 왜 떨어졌는지 알려 주세요", "ko"),
        ("韓國 레이더의 探知率이 낮은 原因을 찾아 주세요", "ko"),
        ("「検出率」이라는 표현은 무슨 뜻인가요?", "ko"),
        # Japanese: kana means ja, never Chinese, even when most characters are kanji.
        ("レーダーの検出率が下がった原因を教えてください", "ja"),
        ("日本語の資料を読んで検出率を説明してください", "ja"),
        ("レーダー", "ja"),  # katakana loanword alone
        ("CFARのしきい値が上がる理由", "ja"),  # kana with a Latin term
        ("ｶﾀｶﾅ", "ja"),  # half-width katakana
        ("検出率の低下", "ja"),
        # Kanji only: no kana, so it follows the Simplified Chinese rule.
        ("検出率低下原因", "zh-Hans"),
        # Korean wins over kana and Hanja.
        ("레이더의 検出率 を 설명해 주세요", "ko"),
        # Simplified Chinese, plain and mixed with Latin terms.
        ("为什么雷达的检测率下降了?", "zh-Hans"),
        ("请解释 SNR 与 RCS 的关系", "zh-Hans"),
        # Traditional Chinese: more Traditional-only than Simplified-only characters.
        ("探測率的資料問題", "zh-Hant"),
        ("為什麼雷達的檢測率下降了?這與軟體有關嗎", "zh-Hant"),
        # Traditional text that uses none of the listed characters is read as Simplified.
        ("雷達偵測率下降原因", "zh-Hans"),
        # Mixed text with more Simplified-only than Traditional-only characters is Simplified.
        ("这个問题", "zh-Hans"),
        # English, including a stray Chinese term.
        ("Why did the detection rate of the radar drop?", "en"),
        ("What does 关系 mean in this paper about radar detection?", "en"),
        # Nothing to read.
        ("", None),
        ("0.90 >= 12 %", None),
    ],
)
def test_detection_table(question: str, expected: str | None):
    assert detect_question_language(question) == expected


def test_an_explicit_choice_always_wins_over_what_the_question_looks_like():
    chinese = "为什么雷达的检测率下降了?"
    assert resolve_response_language("ko", chinese) == "ko"
    assert resolve_response_language("en", chinese) == "en"
    assert resolve_response_language("zh-Hans", "한국어 질문") == "zh-Hans"
    assert resolve_response_language("ja", chinese) == "ja"
    assert resolve_response_language("ko", "レーダー") == "ko"
    assert resolve_response_language(None, chinese) == "zh-Hans"
    with pytest.raises(ValueError, match="RESPONSE_LANGUAGE_UNSUPPORTED"):
        resolve_response_language("fr", chinese)
