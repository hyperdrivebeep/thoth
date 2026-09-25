"""Advertise verified xAI Responses models. No secrets, no Codex effort table reuse."""

from pathlib import Path

from thoth.adapters.models.xai_oauth import xai_model_entries
from thoth.domain.model_settings import ModelOption, ModelSelection


class OmoXaiModelCatalog:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root

    def defaults(self) -> ModelSelection:
        return ModelSelection()

    def options(self) -> tuple[ModelOption, ...]:
        return tuple(
            ModelOption(
                provider="xai",
                model=str(entry["id"]),
                reasoning_efforts=tuple(str(item) for item in entry["efforts"]),
                default_effort="high" if "high" in entry["efforts"] else None,
                capability_source="omo-xai-models-store/openai-responses-v1",
            )
            for entry in xai_model_entries(self.root)
        )
