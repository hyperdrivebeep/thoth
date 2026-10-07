"""A badly typed effect flag is refused at the action entry and nothing is stored."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime
from tests.integration.source_time_fixture import confirm_synthetic_source_time
from tests.integration.test_action_full_rpc import request, value


def _specification(flag: str, flag_value: object) -> dict[str, object]:
    return {
        "description": "Change something",
        "expected_observation_or_change": {"description": "something changes"},
        "effect_completeness_confirmed": True,
        "stop_conditions": ["always"],
        "observability": "receipt",
        "effect_vector": {"effect_completeness_confirmed": True, flag: flag_value},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["yes", 1, None])
async def test_a_forbidden_effect_written_as_a_word_or_number_is_refused_and_nothing_is_stored(
    tmp_path: Path, bad: object
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "inbox").mkdir(parents=True)
    (workspace / "inbox" / "evidence.md").write_text("# Evidence\n\nA result.\n", encoding="utf-8")
    runtime = create_runtime(workspace)
    project_id = "project:effect-type"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "et-project",
                    {
                        "project_id": project_id,
                        "name": "Effect type",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        connected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "et-source",
                    {
                        "project_id": project_id,
                        "relative_path": "evidence.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                        "expected_project_revision": 0,
                    },
                )
            )
        )
        await confirm_synthetic_source_time(runtime, project_id, connected, key="et-source")
        evidence = value(
            await runtime.bus.dispatch(
                request("evidence/list", "et-evidence", {"project_id": project_id})
            )
        )
        refs = [
            str(item["span_id"]) for item in cast(list[dict[str, JsonValue]], evidence["spans"])
        ]
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "et-thread",
                    {"project_id": project_id, "thread_id": "thread:et", "problem": "What now?"},
                )
            )
        )
        object_id = str(cast(list[str], started["current_object_ids"])[0])

        def create(key: str, flag_value: object):
            return runtime.bus.dispatch(
                request(
                    "action/create",
                    key,
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "portfolio_id": "action-portfolio:et",
                        "primary_purpose": "GOVERNANCE_ESCALATION",
                        "specification": _specification("grants_waiver", flag_value),
                        "evidence_refs": refs,
                    },
                )
            )

        refused = await create("et-bad", bad)
        assert refused.error is not None
        assert "grants_waiver" in refused.error.message and "true or false" in refused.error.message
        listed = value(
            await runtime.bus.dispatch(
                request("action/list", "et-list", {"project_id": project_id})
            )
        )
        assert listed["actions"] == []
        accepted = value(await create("et-good", True))
        action = cast(dict[str, JsonValue], accepted["action"])
        assert action["risk_tier"] == "R4"
    finally:
        runtime.close()
