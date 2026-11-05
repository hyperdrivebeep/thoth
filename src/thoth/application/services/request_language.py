"""Admission and model propagation for an immutable request's language."""

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thoth.domain.research_execution import ResearchWork

from pydantic import BaseModel, JsonValue

from thoth.domain.model import ModelRequest
from thoth.domain.research_request import (
    LANGUAGE_REQUEST_TYPES,
    ThreadRequestRevision,
    request_with_language,
)
from thoth.domain.response_language import (
    LANGUAGE_POLICY_VERSION,
    ResponseLanguage,
    explicit_language,
)
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode

_PARTIAL_DRAFT_PREFIX: dict[ResponseLanguage, str] = {
    "ko": "검증 보류 · 부분 초안: ",
    "en": "Review on hold · partial draft: ",
    "zh-Hans": "复核暂缓 · 部分草稿\uff1a",
}


def apply_request_language(work: "ResearchWork", request: ThreadRequestRevision) -> None:
    if isinstance(request, LANGUAGE_REQUEST_TYPES):
        work.response_language = request.response_language
        work.context["response_language"] = request.response_language
        work.result_only["response_language"] = request.response_language


def with_result_language(
    result: dict[str, object], request: ThreadRequestRevision
) -> dict[str, object]:
    return (
        {**result, "response_language": request.response_language}
        if isinstance(request, LANGUAGE_REQUEST_TYPES)
        else result
    )


def partial_answer(answer: str, language: str | None) -> str:
    # Without an explicit language the legacy Korean prefix stays.
    key = "ko" if language is None else explicit_language(language)
    return _PARTIAL_DRAFT_PREFIX[key] + answer


def script_check_problem(language: ResponseLanguage | None, problem: str) -> str:
    """The question text the Hangul-question script checks run against for this answer language.

    Korean answers are checked as a Hangul question. A Chinese answer has no Hangul to demand, so
    its basis has none (the checks then pass it); English has its own prose check and the
    question as asked is kept for it, as it is without an explicit language.
    """
    if language == "ko":
        return "한국어 " + problem
    if language == "zh-Hans":
        return ""
    return problem


def validation_problem(problem: str) -> str:
    from thoth.domain.research_execution import research_work

    work = research_work.get()
    if work is None or work.response_language is None:
        return problem
    return script_check_problem(work.response_language, problem)


def legacy_start_params(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    separated = {
        "contract_version",
        "provider",
        "model",
        "reasoning_effort",
        "origin",
        "response_language",
    }
    return {k: v for k, v in value.items() if k not in separated}


def research_start_params(
    value: dict[str, JsonValue], thread_id: JsonValue
) -> dict[str, JsonValue]:
    return {
        "project_id": value["project_id"],
        "thread_id": thread_id,
        "instruction": value["problem"],
        "provider": value.get("provider", "default"),
        **{k: value.get(k) for k in ("model", "reasoning_effort")},
        **{k: value[k] for k in ("origin", "response_language") if k in value},
    }


def validate_request_language(value: dict[str, JsonValue]) -> None:
    if "response_language" not in value:
        return
    try:
        explicit_language(value["response_language"])
    except ValueError as exc:
        raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, str(exc)) from exc


def with_request_language(
    request: ThreadRequestRevision, value: dict[str, JsonValue]
) -> ThreadRequestRevision:
    validate_request_language(value)
    if "response_language" not in value:
        return request
    return request_with_language(request, explicit_language(value["response_language"]))


def resume_language(
    request: ThreadRequestRevision, value: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    validate_request_language(value)
    language = request.response_language if isinstance(request, LANGUAGE_REQUEST_TYPES) else None
    if "response_language" in value and value["response_language"] != language:
        raise RpcApplicationError(RpcErrorCode.DOMAIN_REJECTED, "REQUEST_LANGUAGE_CONFLICT")
    return {} if language is None else {"response_language": language}


def model_request_language[T: BaseModel](
    request: ModelRequest[T], language: str | None
) -> ModelRequest[T]:
    if language is None:
        return request
    return replace(
        request,
        response_language=explicit_language(language),
        context_pack=request.context_pack.model_copy(
            update={
                "research_context": {
                    **request.context_pack.research_context,
                    "response_language": explicit_language(language),
                    "language_policy_version": LANGUAGE_POLICY_VERSION,
                },
            }
        ),
    )
