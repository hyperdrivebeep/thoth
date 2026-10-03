"""Advertise verified xAI Responses models. No secrets, no Codex effort table reuse."""

from pathlib import Path

from thoth.adapters.models.xai_broker import XaiAuthBroker, broker_for_workspace
from thoth.domain.model_settings import ModelOption, ModelSelection


class ThothXaiOAuthCatalog:
    """THOTH-curated metadata from senpi-ai 2026.9.26 xai.json SHA256
    9b23faa5218e966a17108c05d2498a617ade1eae9b0d0684284c77d67949a3f1.
    Catalog visibility is local authorization state, not remote entitlement proof.
    """

    fallback_default_allowed = False

    def __init__(self, workspace: Path, *, broker: XaiAuthBroker | None = None) -> None:
        self.broker = broker or broker_for_workspace(workspace)

    def defaults(self) -> ModelSelection:
        return ModelSelection()

    def options(self) -> tuple[ModelOption, ...]:
        if self.broker.status().get("execution_eligible") is not True:
            return ()
        return (
            ModelOption(
                provider="xai-oauth",
                model="grok-4.6",
                reasoning_efforts=("low", "medium", "high", "xhigh"),
                default_effort=None,
                capability_source="thoth-curated/senpi-ai-2026.9.26-xai-json",
            ),
        )
