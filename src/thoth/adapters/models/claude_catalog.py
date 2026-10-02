"""THOTH-curated Claude Messages option; no external model-cache dependency."""

from __future__ import annotations

from pathlib import Path

from thoth.adapters.models.claude_oauth import ClaudeOAuthBroker, broker_for_workspace
from thoth.domain.model_settings import ModelOption, ModelSelection


class ClaudeOAuthCatalog:
    """Metadata from senpi-ai 2026.9.26 anthropic.json SHA256
    f9db437d92449fb860a2d457a6019cdfd51c11180c08df2b5f14d75ade773953.
    This is local option metadata, not OAuth entitlement or live execution proof.
    """

    fallback_default_allowed = False

    def __init__(self, workspace: Path, *, broker: ClaudeOAuthBroker | None = None) -> None:
        self.broker = broker or broker_for_workspace(workspace)

    def defaults(self) -> ModelSelection:
        return ModelSelection()

    def options(self) -> tuple[ModelOption, ...]:
        if self.broker.status().get("execution_eligible") is not True:
            return ()
        return (
            ModelOption(
                provider="claude-oauth",
                model="claude-sonnet-4-5-20250929",
                # Official model-specific effort support excludes Sonnet 4.5.
                # reasoning=true in installed metadata is not an effort map.
                reasoning_efforts=(),
                default_effort=None,
                capability_source="thoth-curated/senpi-ai-2026.9.26-anthropic-json",
            ),
        )
