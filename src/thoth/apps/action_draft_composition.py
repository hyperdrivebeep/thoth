"""Compose the action draft made from a discriminating test."""

from __future__ import annotations

from thoth.application.commands.action_from_test import ActionFromTestHandlers
from thoth.application.services.hypothesis_link_view import HypothesisLinkReader
from thoth.application.services.request_records import RequestRecords
from thoth.protocol.registry import MethodRegistry


def install_action_drafts(registry: MethodRegistry, records: RequestRecords) -> None:
    """Call after the hypothesis links are installed, so the draft goes through the same guard."""
    handlers = ActionFromTestHandlers(
        reader=HypothesisLinkReader(records.ledger), create=registry.resolve("action/create")
    )
    registry.register("action/draft/fromTest", handlers.draft)
