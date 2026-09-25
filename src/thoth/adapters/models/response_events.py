"""Parse only observed response metadata; never retain raw error text."""

from pydantic import BaseModel, Field, StrictInt


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


class ResponseMetadata(BaseModel):
    id: str | None = None
    output: tuple[ResponseItem, ...] = ()
    usage: ResponseUsage | None = None


class ResponseEvent(BaseModel):
    type: str
    delta: str = ""
    item: ResponseItem | None = None
    response: ResponseMetadata | None = None
