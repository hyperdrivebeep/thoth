"""The xAI session value supplied by THOTH-owned authentication readers."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class XaiSession:
    access_token: str = field(repr=False)
    model: str
    reasoning_effort: str | None = None
