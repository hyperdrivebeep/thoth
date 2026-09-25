"""Read the selected xAI OAuth item only. Never persist or stringify secrets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast

from thoth.ports.model import ModelExecutionHold

_ALLOWED_API = "openai-responses"
_ALLOWED_BASE = "https://api.x.ai/v1"
_ALLOWED_EFFORT = frozenset({"low", "medium", "high", "xhigh"})


class XaiSessionUnavailable(ModelExecutionHold):
    pass


@dataclass(frozen=True)
class XaiSession:
    access_token: str = field(repr=False)
    model: str
    reasoning_effort: str | None = None


class XaiModelEntry(TypedDict):
    id: str
    efforts: tuple[str, ...]


class OmoXaiSessionReader:
    def __init__(self, root: Path | None = None, model: str | None = None) -> None:
        self.root = root
        self.model = model

    def read(self) -> XaiSession:
        if self.root is None:
            raise XaiSessionUnavailable("XAI_CURRENT_SESSION_UNAVAILABLE")
        try:
            from thoth.adapters.models.local_credentials import LocalCredentialHold, local_secret
        except ImportError:
            pass
        else:
            try:
                token = local_secret("xai", self.root)
            except (LocalCredentialHold, ImportError):
                pass
            else:
                return XaiSession(token, self.model or "grok-4.6")
        try:
            auth: object = json.loads((self.root / "auth.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError) as exc:
            raise XaiSessionUnavailable("XAI_CURRENT_SESSION_UNAVAILABLE") from exc
        item = cast(dict[object, object], auth).get("xai") if isinstance(auth, dict) else None
        if not isinstance(item, dict):
            raise XaiSessionUnavailable("XAI_OAUTH_ITEM_UNAVAILABLE")
        fields = cast(dict[object, object], item)
        kind = fields.get("type")
        if kind == "oauth":
            token = fields.get("access")
        elif kind == "api_key":
            token = fields.get("key") or fields.get("apiKey")
        else:
            raise XaiSessionUnavailable("XAI_OAUTH_ITEM_UNAVAILABLE")
        if not isinstance(token, str) or not token:
            raise XaiSessionUnavailable("XAI_OAUTH_ITEM_UNAVAILABLE")
        model = self.model
        if not isinstance(model, str) or not model:
            raise XaiSessionUnavailable("XAI_MODEL_REQUIRED")
        return XaiSession(token, model)


def xai_effort_map(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        return ()
    efforts: list[str] = []
    for key, value in cast(dict[object, object], raw).items():
        if value is None or not isinstance(value, str):
            continue
        if value not in _ALLOWED_EFFORT:
            continue
        if key in _ALLOWED_EFFORT and value == key:
            efforts.append(value)
    return tuple(dict.fromkeys(efforts))


def xai_model_entries(root: Path | None = None) -> tuple[XaiModelEntry, ...]:
    path = (root or Path.home() / ".omo" / "agent") / "models-store.json"
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return ()
    block = cast(dict[object, object], raw).get("xai") if isinstance(raw, dict) else None
    models = cast(dict[object, object], block).get("models") if isinstance(block, dict) else None
    if not isinstance(models, list):
        return ()
    entries: list[XaiModelEntry] = []
    for item in cast(list[object], models):
        if not isinstance(item, dict):
            continue
        fields = cast(dict[object, object], item)
        model_id = fields.get("id")
        api = fields.get("api")
        base = fields.get("baseUrl")
        if not isinstance(model_id, str) or api != _ALLOWED_API or base != _ALLOWED_BASE:
            continue
        entries.append(
            {
                "id": model_id,
                "efforts": xai_effort_map(fields.get("thinkingLevelMap")),
            }
        )
    return tuple(entries)
