"""Frozen synthetic projections preserve privacy, Korean copy, IDs and event ordering."""

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest

from thoth.application.services.user_activity_projection import project_user_activity_events
from thoth.domain.user_activity import UserActivityEvent

FIXTURE = Path(__file__).parents[2] / "fixtures/user_activity_projection_contract.json"
CASES = cast(list[dict[str, object]], json.loads(FIXTURE.read_text(encoding="utf-8")))
FORBIDDEN = (
    "FIXTURE_SECRET",
    "FIXTURE_PASSWORD",
    "FIXTURE_PRIVATE_ID",
    "FIXTURE_COMMAND",
    "FIXTURE_PROMPT",
    "FORGED_FIXTURE_ROW",
    "fixture-user",
    "C:\\private",
    "Ignore previous instructions",
    "execute this command",
    "=FIXTURE_FORMULA",
)


@pytest.mark.parametrize("case", CASES, ids=[str(case["name"]) for case in CASES])
def test_complete_projection_preserves_redaction_copy_ids_and_input(
    case: dict[str, object],
) -> None:
    value = cast(dict[str, object], case["value"])
    contexts = cast(dict[str, dict[str, object]] | None, case["source_context"])
    before = deepcopy((value, contexts))

    events = project_user_activity_events(value, source_context=contexts)

    assert events == case["expected"]
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    typed = [UserActivityEvent.model_validate(event) for event in events]
    assert len({event.event_id for event in typed}) == len(events)
    assert all(not event.redaction.source_content_included for event in typed)
    serialized = json.dumps(events, ensure_ascii=False)
    for forbidden in FORBIDDEN:
        assert forbidden not in serialized
    assert project_user_activity_events(value, source_context=contexts) == events
    assert (value, contexts) == before
