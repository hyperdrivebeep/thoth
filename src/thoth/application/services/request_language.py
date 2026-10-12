"""Admission and model propagation for an immutable request's language."""

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thoth.domain.research_execution import ResearchWork

from pydantic import BaseModel, JsonValue

from thoth.domain.model import ModelRequest
from thoth.domain.research_request import ThreadRequestRevision, ThreadRequestRevisionV21
from thoth.domain.response_language import LANGUAGE_POLICY_VERSION, explicit_language
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def apply_request_language(work: "ResearchWork", request: ThreadRequestRevision) -> None:
    if isinstance(request, ThreadRequestRevisionV21):
        work.response_language = request.response_language
        work.context["response_language"] = request.response_language
        work.result_only["response_language"] = request.response_language


def with_result_language(
    result: dict[str, object], request: ThreadRequestRevision
) -> dict[str, object]:
    return (
        {**result, "response_language": request.response_language}
        if isinstance(request, ThreadRequestRevisionV21)
        else result
    )


def partial_answer(answer: str, language: str | None) -> str:
    prefix = "Review on hold · partial draft: " if language == "en" else "검증 보류 · 부분 초안: "
    return prefix + answer


def validation_problem(problem: str) -> str:
    from thoth.domain.research_execution import research_work

    work = research_work.get()
    if work is None or work.response_language is None:
        return problem
    return "한국어 " + problem if work.response_language == "ko" else problem


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
    return ThreadRequestRevisionV21.model_validate(
        {
            **request.model_dump(),
            "schema_version": "2.1.0",
            "response_language": value["response_language"],
        }
    )


def resume_language(
    request: ThreadRequestRevision, value: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    validate_request_language(value)
    language = request.response_language if isinstance(request, ThreadRequestRevisionV21) else None
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
