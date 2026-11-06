"""Parse only observed response metadata; never retain raw error text."""

from typing import cast, get_args

from pydantic import BaseModel, Field, StrictInt, field_validator

from thoth.domain.model_dispatch import RemoteErrorCode


class ResponseUsage(BaseModel):
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    input_tokens_details: dict[str, object] | None = None

    model_config = {"extra": "ignore"}


class ResponsePart(BaseModel):
    type: str
    text: str = ""


class ResponseItem(BaseModel):
    type: str
    content: tuple[ResponsePart, ...] = ()


class ResponseErrorInfo(BaseModel):
    """Only the error code of a failed response. The message is not declared, so it is not read."""

    code: str | None = None

    model_config = {"extra": "ignore"}

    @field_validator("code", mode="before")
    @classmethod
    def _code_text(cls, value: object) -> object:
        # An odd shape must never turn into an invalid-stream failure of its own.
        return value if isinstance(value, str) else None


def _object_or_none(value: object) -> dict[str, object] | None:
    """The value if it is a JSON object, otherwise None; the model then checks the keys it reads."""

    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


class ResponseMetadata(BaseModel):
    id: str | None = None
    output: tuple[ResponseItem, ...] = ()
    usage: ResponseUsage | None = None
    error: ResponseErrorInfo | None = None

    @field_validator("error", mode="before")
    @classmethod
    def _error_shape(cls, value: object) -> object:
        return _object_or_none(value)


class ResponseEvent(BaseModel):
    type: str
    delta: str = ""
    item: ResponseItem | None = None
    response: ResponseMetadata | None = None
    # The code of an `error` event, at the top (OpenAI SDK ResponseErrorEvent.code) or nested as
    # in the error bodies http_rejection.py reads. The event's message is not declared, so not read.
    code: str | None = None
    error: ResponseErrorInfo | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _code_text(cls, value: object) -> object:
        return value if isinstance(value, str) else None

    @field_validator("error", mode="before")
    @classmethod
    def _error_shape(cls, value: object) -> object:
        return _object_or_none(value)


# Error codes that may be recorded. Anything else a remote sends is recorded as OTHER, and a
# missing code as None; no error message is ever stored. Evidence for each value:
# - server_error, rate_limit_exceeded, invalid_prompt, data_residency_mismatch, bio_policy and
#   vector_store_timeout are the non-image values of ResponseError.code in the OpenAI Python SDK
#   2.54.0 (openai/types/responses/response_error.py, generated from the API's OpenAPI spec): the
#   code of a failed response. The image codes are left out because this product sends no image.
# - insufficient_quota and usage_limit_reached are already classified by http_rejection.py and
#   used in tests/unit/models (test_http_rejection_metadata.py, test_interrupted_call_retry.py).
# The SDK types the code of an `error` event only as a string, so these values are not promised
# for it. A link to the official documentation was not checked.
OBSERVED_ERROR_CODES = frozenset(get_args(RemoteErrorCode)) - {"OTHER"}


def observed_error_code(event: ResponseEvent) -> RemoteErrorCode | None:
    """The recordable code of an error or failed-response event: a listed code, OTHER, or None."""

    if event.type == "error":
        code = event.code or (None if event.error is None else event.error.code)
    elif event.type == "response.failed" and event.response is not None:
        code = None if event.response.error is None else event.response.error.code
    else:
        return None
    if code is None:
        return None
    return cast(RemoteErrorCode, code) if code in OBSERVED_ERROR_CODES else "OTHER"
