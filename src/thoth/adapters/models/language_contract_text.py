"""Provider-facing language policy; legacy wording is byte-for-byte preserved."""

from pydantic import BaseModel

from thoth.domain.model import ModelRequest
from thoth.domain.response_language import ResponseLanguage, explicit_language, language_instruction


def language_for(request: ModelRequest[BaseModel]) -> ResponseLanguage | None:
    return (
        explicit_language(request.response_language)
        if request.response_language is not None
        else None
    )


def user_visible_language_contract(request: ModelRequest[BaseModel] | None = None) -> str:
    if request is not None and (language := language_for(request)) is not None:
        return (
            language_instruction(language)
            + " "
            + (_PLAIN_WORDING if language == "ko" else _PLAIN_WORDING_EN)
        )
    return (
        "Write user-facing prose in the same language as context_pack.problem; "
        "do not switch that prose to English when the question is not English. "
        "Keep identifiers, GPU/model names, table numbers, units, "
        "span IDs, file names, and quoted source text exactly as supplied. " + _PLAIN_WORDING
    )


_PLAIN_WORDING = (
    "Write so the person who asked can read it at once, in short plain sentences. For a Korean "
    "question write easy Korean. Keep the technical terms, units and standard abbreviations "
    "that appear in the user's own material (for example SNR, RCS, CFAR, dB) exactly as they "
    "are. Do not use program-internal codes, field names, variable names or made-up ids in "
    "prose; say conditions in words (write '탐지율 0.90 이상', not 'detection_rate >= 0.90 "
    "ratio') and use Korean words for internal terms such as sandbox-owner or gap. Write the "
    "FACT/UNKNOWN labels as '사실' and '확인 불가'."
)


_PLAIN_WORDING_EN = (
    "Write so the person who asked can read it at once, in short plain sentences. "
    "Keep the technical terms, units and standard abbreviations that appear in the user's "
    "own material (for example SNR, RCS, CFAR, dB) exactly as they are. "
    "Do not use program-internal codes, field names, variable names or made-up ids in prose; "
    "say conditions in words (write 'detection rate at least 0.90', not "
    "'detection_rate >= 0.90 ratio'). Use plain English for internal terminology. "
    "Write FACT/UNKNOWN display labels as 'Fact' and 'Unknown'; preserve schema enum values."
)
