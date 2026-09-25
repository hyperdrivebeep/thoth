from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value

from thoth.apps.runtime import create_runtime


@pytest.mark.asyncio
async def test_all_six_versioned_profile_packs_load_through_the_same_runtime(
    tmp_path: Path,
) -> None:
    runtime = create_runtime(tmp_path / "all-profile-packs")
    try:
        result = value(
            await runtime.bus.dispatch(
                request(
                    "criteria/profile/list",
                    "a09-all-packs",
                    {"project_id": "project:a09:profiles"},
                )
            )
        )
        profiles = cast(list[dict[str, JsonValue]], result["profiles"])
        assert [item["profile_ref"] for item in profiles] == [
            "ACCREDITED_LAB_CONFORMITY",
            "AI_TEVV",
            "CLINICAL_STATISTICAL_ANALYSIS",
            "GENERAL_RND",
            "NATIONAL_RND_PERFORMANCE",
            "SYSTEMS_ENGINEERING_VERIFICATION",
        ]
        for profile in profiles:
            assert profile["version"] == 1
            assert profile["applicability_terms"]
            assert profile["measurement_validity"]
            assert profile["uncertainty_policy"]
            assert profile["decision_rules"]
            assert profile["entry_criteria"]
            assert profile["exit_criteria"]
            assert profile["change_authority"]
    finally:
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile_ref", "text"),
    (
        (
            "SYSTEMS_ENGINEERING_VERIFICATION",
            "system requirements verification inspection acceptance procedure",
        ),
        (
            "ACCREDITED_LAB_CONFORMITY",
            "ISO 17025 accredited laboratory calibration traceability",
        ),
        (
            "CLINICAL_STATISTICAL_ANALYSIS",
            "clinical trial endpoint confidence interval prespecified protocol",
        ),
        ("AI_TEVV", "AI TEVV robustness red team dataset drift"),
        (
            "NATIONAL_RND_PERFORMANCE",
            "national ministry R&D program performance indicator",
        ),
    ),
)
async def test_each_specialized_pack_enters_the_normal_thread_hold_path(
    tmp_path: Path,
    profile_ref: str,
    text: str,
) -> None:
    runtime, connector, project_id = await prepare_thread(
        tmp_path / profile_ref.casefold(),
        allow_connector=True,
    )
    connector.payloads["profile.md"] = text.encode()
    thread_id = f"thread:{project_id}:profile:{profile_ref.casefold()}"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    f"a09-source-{profile_ref}",
                    {
                        "project_id": project_id,
                        "connector_id": "a02-readonly",
                        "selector": {"relative_path": "profile.md"},
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    f"a09-thread-{profile_ref}",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "problem": f"Which {profile_ref} criterion applies?",
                        "scope": {"workstream": "profile-routing"},
                    },
                )
            )
        )
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    f"a09-input-{profile_ref}",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        decision = cast(dict[str, JsonValue], analyzed["criterion_profile_decision"])
        assert decision["state"] == "PROFILE_DECISION_REQUIRED"
        assert profile_ref in cast(list[str], decision["candidate_profile_refs"])
        assert decision["selected_profile_refs"] == []
    finally:
        runtime.close()
