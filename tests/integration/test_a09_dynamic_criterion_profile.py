from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value


@pytest.mark.asyncio
async def test_normal_non_system_thread_routes_to_general_rnd_without_system_default(
    tmp_path: Path,
) -> None:
    runtime, _connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    try:
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a09-general-thread",
                    {"project_id": project_id, "thread_id": f"thread:{project_id}"},
                )
            )
        )
        decision = cast(dict[str, JsonValue], analyzed["criterion_profile_decision"])
        assert decision["state"] == "SELECTED"
        assert decision["selected_profile_refs"] == ["GENERAL_RND"]
        assert decision["candidate_profile_refs"] == []
        assert decision["profile_decision_required"] is False
        assert "SYSTEMS_ENGINEERING_VERIFICATION" not in cast(
            list[str], decision["selected_profile_refs"]
        )
        criteria = value(
            await runtime.bus.dispatch(
                request("criteria/list", "a09-general-criteria", {"project_id": project_id})
            )
        )
        contracts = cast(list[dict[str, JsonValue]], criteria["criteria"])
        assert contracts
        assert contracts[0]["profile_refs"] == ["GENERAL_RND"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_systems_engineering_candidate_holds_when_decision_dimensions_change(
    tmp_path: Path,
) -> None:
    runtime, connector, project_id = await prepare_thread(tmp_path, allow_connector=True)
    connector.payloads["systems.md"] = (
        b"# Systems engineering verification\n\n"
        b"The system requirement shall use an approved verification test procedure "
        b"and an explicit acceptance criterion.\n"
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "a09-system-source",
                    {
                        "project_id": project_id,
                        "connector_id": "a02-readonly",
                        "selector": {"relative_path": "systems.md"},
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        thread_id = f"thread:{project_id}:systems"
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "a09-system-thread",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "problem": "Which systems engineering verification procedure applies?",
                        "scope": {"workstream": "system-verification"},
                    },
                )
            )
        )
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "a09-system-input",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        decision = cast(dict[str, JsonValue], analyzed["criterion_profile_decision"])
        assert decision["state"] == "PROFILE_DECISION_REQUIRED"
        assert decision["selected_profile_refs"] == []
        assert "SYSTEMS_ENGINEERING_VERIFICATION" in cast(
            list[str], decision["candidate_profile_refs"]
        )
        changes = cast(dict[str, JsonValue], decision["decision_dimension_changes"])
        system_changes = cast(list[str], changes["SYSTEMS_ENGINEERING_VERIFICATION"])
        assert "REQUIRED_EVIDENCE" in system_changes
        assert "EVALUATOR" in system_changes
        assert "EXIT_CRITERION" in system_changes
        assert decision["profile_decision_required"] is True
        assert decision["hold_reason"]
        criteria = value(
            await runtime.bus.dispatch(
                request("criteria/list", "a09-system-criteria", {"project_id": project_id})
            )
        )
        assert criteria["criteria"] == []
    finally:
        runtime.close()
