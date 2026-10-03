"""Approval is bound to what is sent, and a later change shows exactly what moved."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a04_storage_authority import (
    approve_required_roles,
    prepare_r3,
    start_request,
)

from thoth.apps.runtime_types import AppRuntime

CONTENT: dict[str, Any] = {
    "body_text": "안녕하세요 담당자님 시험 성적서를 보내 주세요",
    "attachments": [{"name": "요청서.pdf", "digest": "1" * 64}],
}


async def revise_step(
    runtime: AppRuntime, project: str, plan: dict[str, Any], patch: dict[str, object], key: str
) -> dict[str, Any]:
    return value(
        await runtime.bus.dispatch(
            request(
                "action/step/revise",
                key,
                {
                    "project_id": project,
                    "plan_id": plan["plan_id"],
                    "step_id": "step:r3",
                    "patch": patch,
                    "evidence_refs": [],
                    "reason": "test change",
                    "expected_plan_revision": plan["revision_digest"],
                },
            )
        )
    )["plan"]


async def read_authorization(
    runtime: AppRuntime, project: str, authorization: dict[str, Any], key: str
) -> dict[str, Any]:
    return value(
        await runtime.bus.dispatch(
            request(
                "action/authorization/read",
                key,
                {"project_id": project, "authorization_id": authorization["authorization_id"]},
            )
        )
    )


async def reapprove(
    runtime: AppRuntime, project: str, plan: dict[str, Any], key: str
) -> dict[str, Any]:
    prepared = value(
        await runtime.bus.dispatch(
            request(
                "action/authorization/prepare",
                key + "-prepare",
                {
                    "project_id": project,
                    "plan_id": plan["plan_id"],
                    "step_id": "step:r3",
                    "plan_revision_digest": plan["revision_digest"],
                    "predecessor_output_digests": [],
                    "target_baseline_digests": ["b" * 64],
                    "policy_version": "policy:current",
                },
            )
        )
    )["authorization"]
    return await approve_required_roles(runtime, project, prepared, "human:owner", key)


@pytest.mark.asyncio
async def test_new_envelopes_are_2_0_0_bound_to_the_payload_not_the_plan_revision(
    tmp_path: Path,
) -> None:
    runtime, project, plan, approved = await prepare_r3(tmp_path / "w")
    try:
        assert approved["state"] == "APPROVED"
        read = (await read_authorization(runtime, project, approved, "read"))["authorization"]
        assert read["schema_version"] == "2.0.0" and len(read["payload_digest"]) == 64
        assert (
            read["payload"]["channel"] == ""
            and read["plan_revision_digest"] == plan["revision_digest"]
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_display_only_change_keeps_the_approval_and_it_still_runs(tmp_path: Path) -> None:
    runtime, project, plan, approved = await prepare_r3(tmp_path / "w")
    try:
        revised = await revise_step(
            runtime, project, plan, {"presentation": {"label": "요청 메일", "order": 2}}, "display"
        )
        assert revised["revision_digest"] != plan["revision_digest"]
        read = await read_authorization(runtime, project, approved, "read")
        assert read["authorization"]["state"] == "APPROVED"
        assert read["payload_current"] is True and read["authorization"]["material_changes"] == []
        started = await runtime.bus.dispatch(start_request(runtime, project, revised, "start"))
        assert started.error is None
        assert (await read_authorization(runtime, project, approved, "after"))["authorization"][
            "state"
        ] == "CONSUMED"
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_one_changed_body_character_makes_the_approval_stale_and_shows_the_words(
    tmp_path: Path,
) -> None:
    runtime, project, plan, approved = await prepare_r3(tmp_path / "w")
    try:
        first = await revise_step(runtime, project, plan, {"content": CONTENT}, "content")
        approved = await reapprove(runtime, project, first, "second")
        changed = await revise_step(
            runtime,
            project,
            first,
            {"content": {**CONTENT, "body_text": CONTENT["body_text"] + "."}},
            "edit",
        )
        read = await read_authorization(runtime, project, approved, "stale")
        record = read["authorization"]
        assert record["state"] == "STALE" and record["stale_reason"] == "MATERIAL_CHANGE"
        (change,) = [item for item in record["material_changes"] if item["field"] == "body"]
        assert change["label"] == "본문" and any(seg["op"] == "add" for seg in change["words"])
        assert read["payload_current"] is False
        # the earlier approval is preserved as history and still names the version it covered
        assert (
            record["decision_history"]
            and record["plan_revision_digest"] == first["revision_digest"]
        )
        # Nothing is dispatched on the stale approval: the step stays out of the runnable frontier.
        started = value(
            await runtime.bus.dispatch(start_request(runtime, project, changed, "start"))
        )
        assert started["initial_preflight_frontier"] == []
        assert (await read_authorization(runtime, project, approved, "unchanged"))["authorization"][
            "state"
        ] == "STALE"
    finally:
        runtime.close()


async def recompose(
    runtime: AppRuntime, project: str, plan: dict[str, Any], targets: list[str], key: str
) -> dict[str, Any]:
    step = {
        "step_id": "step:r3",
        "action_ref": plan["selected_action_refs"][0],
        "inputs": [],
        "target_digests": targets,
        "output_contract": {"type": "configuration-receipt"},
        "preconditions": [],
        "stop_conditions": ["unexpected effect"],
        "state": "READY",
        "effect_vector": {"effect_completeness_confirmed": True, "external_write": True},
    }
    return value(
        await runtime.bus.dispatch(
            request(
                "action/plan/compose",
                key,
                {
                    "project_id": project,
                    "object_id": plan["object_id"],
                    "plan_id": plan["plan_id"],
                    "selected_action_refs": plan["selected_action_refs"],
                    "step_candidates": [step],
                    "dependency_edges": [],
                },
            )
        )
    )["plan"]


@pytest.mark.asyncio
async def test_composing_the_same_plan_again_marks_an_approval_of_changed_content_stale(
    tmp_path: Path,
) -> None:
    runtime, project, plan, approved = await prepare_r3(tmp_path / "w")
    try:
        same = await recompose(runtime, project, plan, ["b" * 64], "same")
        assert same["revision_digest"] != plan["revision_digest"]
        kept = await read_authorization(runtime, project, approved, "kept")
        assert kept["authorization"]["state"] == "APPROVED" and kept["payload_current"] is True
        changed = await recompose(runtime, project, same, ["c" * 64], "changed")
        assert changed["supersedes_revision_digest"] == same["revision_digest"]
        read = await read_authorization(runtime, project, approved, "stale")
        record = read["authorization"]
        assert record["state"] == "STALE" and record["stale_reason"] == "MATERIAL_CHANGE"
        assert [item["field"] for item in record["material_changes"]] == ["targets"]
        assert read["payload_current"] is False
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_swapped_attachment_shows_name_and_digest_before_and_after(tmp_path: Path) -> None:
    runtime, project, plan, approved = await prepare_r3(tmp_path / "w")
    try:
        first = await revise_step(runtime, project, plan, {"content": CONTENT}, "content")
        approved = await reapprove(runtime, project, first, "second")
        await revise_step(
            runtime,
            project,
            first,
            {
                "content": {
                    **CONTENT,
                    "attachments": [{"name": "요청서-v2.pdf", "digest": "2" * 64}],
                }
            },
            "swap",
        )
        record = (await read_authorization(runtime, project, approved, "stale"))["authorization"]
        (change,) = [item for item in record["material_changes"] if item["field"] == "attachments"]
        (pair,) = change["attachments"]
        assert (pair["before_name"], pair["before_digest"]) == ("요청서.pdf", "1" * 64)
        assert (pair["after_name"], pair["after_digest"]) == ("요청서-v2.pdf", "2" * 64)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_an_unknown_new_step_field_needs_a_new_approval(tmp_path: Path) -> None:
    runtime, project, plan, approved = await prepare_r3(tmp_path / "w")
    try:
        await revise_step(
            runtime, project, plan, {"cc_recipients": ["outside@example.com"]}, "unknown"
        )
        record = (await read_authorization(runtime, project, approved, "stale"))["authorization"]
        assert record["state"] == "STALE"
        assert [item["label"] for item in record["material_changes"]] == ["알 수 없는 새 항목"]
    finally:
        runtime.close()
