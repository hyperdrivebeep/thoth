"""Read OMO native models-store and auth.json. Never persist or print secrets."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast

from openai import AsyncOpenAI

from thoth.adapters.models.openai_responses import OpenAIResponsesModel
from thoth.adapters.models.registry import RegisteredModelResolver
from thoth.adapters.models.xai_oauth import xai_effort_map
from thoth.domain.model_settings import ModelOption, ModelSelection
from thoth.ports.model import ModelExecutionHold, ModelPort

_RESPONSES_API = "openai-responses"


class OmoNativeUnavailable(ModelExecutionHold):
    pass


@dataclass(frozen=True)
class OmoNativeSession:
    token: str = field(repr=False)
    provider: str
    model: str
    base_url: str
    kind: str


class OmoStoreEntry(TypedDict):
    provider: str
    id: str
    base_url: str
    efforts: tuple[str, ...]


def omo_agent_root(root: Path | None = None) -> Path:
    return root or Path.home() / ".omo" / "agent"


def omo_store_entries(root: Path | None = None) -> tuple[OmoStoreEntry, ...]:
    path = omo_agent_root(root) / "models-store.json"
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return ()
    if not isinstance(raw, dict):
        return ()
    entries: list[OmoStoreEntry] = []
    for provider, block in cast(dict[object, object], raw).items():
        if provider == "version" or not isinstance(provider, str) or not isinstance(block, dict):
            continue
        models = cast(dict[object, object], block).get("models")
        if not isinstance(models, list):
            continue
        for item in cast(list[object], models):
            if not isinstance(item, dict):
                continue
            fields = cast(dict[object, object], item)
            model_id = fields.get("id")
            api = fields.get("api")
            base = fields.get("baseUrl")
            if (
                not isinstance(model_id, str)
                or api != _RESPONSES_API
                or not isinstance(base, str)
                or not base.startswith("https://")
            ):
                continue
            entries.append(
                {
                    "provider": provider,
                    "id": model_id,
                    "base_url": base.rstrip("/"),
                    "efforts": xai_effort_map(fields.get("thinkingLevelMap")),
                }
            )
    return tuple(entries)


def omo_credential(provider: str, root: Path | None = None) -> tuple[str, str]:
    path = omo_agent_root(root) / "auth.json"
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as exc:
        raise OmoNativeUnavailable("OMO_AUTH_UNAVAILABLE") from exc
    item = cast(dict[object, object], raw).get(provider) if isinstance(raw, dict) else None
    if not isinstance(item, dict):
        raise OmoNativeUnavailable("OMO_PROVIDER_AUTH_UNAVAILABLE")
    fields = cast(dict[object, object], item)
    kind = fields.get("type")
    if kind == "oauth":
        token = fields.get("access")
    elif kind == "api_key":
        token = fields.get("key") or fields.get("apiKey")
    else:
        raise OmoNativeUnavailable("OMO_AUTH_TYPE_UNSUPPORTED")
    if not isinstance(token, str) or not token:
        raise OmoNativeUnavailable("OMO_AUTH_SECRET_UNAVAILABLE")
    return token, str(kind)


class OmoNativeCatalog:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def defaults(self) -> ModelSelection:
        options = self.options()
        for option in options:
            if option.provider == "xai" and option.model == "grok-4.6":
                return ModelSelection(
                    provider=option.provider,
                    model=option.model,
                    reasoning_effort=option.default_effort,
                )
        if options:
            option = options[0]
            return ModelSelection(
                provider=option.provider,
                model=option.model,
                reasoning_effort=option.default_effort,
            )
        return ModelSelection()

    def options(self) -> tuple[ModelOption, ...]:
        return tuple(
            ModelOption(
                provider=str(entry["provider"]),
                model=str(entry["id"]),
                reasoning_efforts=tuple(str(item) for item in entry["efforts"]),
                default_effort="high" if "high" in entry["efforts"] else None,
                capability_source="omo-native-models-store/openai-responses-v1",
            )
            for entry in omo_store_entries(self.root)
        )


def omo_responses_model(provider: str, model: str | None, root: Path | None = None) -> ModelPort:
    if not isinstance(model, str) or not model:
        raise OmoNativeUnavailable("OMO_MODEL_REQUIRED")
    token, kind = omo_credential(provider, root)
    base = next(
        (
            str(entry["base_url"])
            for entry in omo_store_entries(root)
            if entry["provider"] == provider and entry["id"] == model
        ),
        None,
    )
    if base is None:
        raise OmoNativeUnavailable("OMO_MODEL_NOT_IN_STORE")
    client = AsyncOpenAI(api_key=token, base_url=base)
    _ = kind
    return OpenAIResponsesModel(client, model_id=model)


def register_omo_native_providers(
    resolver: RegisteredModelResolver, root: Path | None = None
) -> None:
    for option in OmoNativeCatalog(root).options():
        provider = option.provider.strip().lower()
        if resolver.contains(provider):
            continue
        resolver.register(
            provider,
            lambda model, bound=provider: omo_responses_model(bound, model, root),
        )
