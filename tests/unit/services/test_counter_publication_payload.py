"""Complete intent, staging request and committed payload before extraction."""

import json
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from tests.unit.services.counter_wave_support import Harness

from thoth.domain.acquisition import SearchIntent
from thoth.domain.canonical import canonical_payload


class PublicationHarness(Harness):
    def payloads(self) -> dict[str, object]:
        intents = cast(Mock, self._traces.put_search_intent).call_args_list
        stages = cast(Mock, self._evidence_graph.stage_link).call_args_list
        return {
            "intents": [
                cast(SearchIntent, call.args[0]).model_dump(mode="json") for call in intents
            ],
            "stages": [
                json.loads(canonical_payload(cast(dict[str, object], call.kwargs)))
                for call in stages
            ],
            "commits": [commit.model_dump(mode="json") for commit in self.committed],
            "events": self.events,
        }


@pytest.mark.parametrize("side", ["counter", "supports"])
async def test_complete_publication_payload_is_preserved(side: str) -> None:
    h = PublicationHarness((f"{side} first", f"{side} second"))
    await h.run()
    expected = json.loads(Path(__file__).with_name("counter_publication_payload.json").read_text())
    assert h.payloads() == expected[side]
