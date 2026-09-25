from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.storage.control_record import SqliteControlRecordStore


def item() -> dict[str, Any]:
    return {
        "item_id": "followup:one",
        "description": "Collect a later observation",
        "disposition": "DEFERRED",
        "owner_ref": "human:local-user",
        "trigger_or_due": "next observation",
        "residual_risk": {
            "description": "Later evidence could change this draft",
            "state": "UNKNOWN",
            "basis_refs": [],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["item_id", "disposition", "owner_ref", "trigger_or_due", "residual_risk"]
)
async def test_legacy_missing_detail_is_not_ready(tmp_path: Path, field: str) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        incomplete = item()
        del incomplete[field]
        response = value(
            await runtime.bus.dispatch(
                request(
                    "closure/readiness/assess",
                    field,
                    {
                        "project_id": "p",
                        "closure_scope": "THREAD_CYCLE",
                        "scope_ref": "t",
                        "disposition": "DEFERRED",
                        "open_items": [incomplete],
                    },
                )
            )
        )
        readiness = cast(dict[str, Any], response["readiness"])
        assert readiness["state"] == "NOT_READY"
        assert readiness["payload"]["open_item_issues"]
        assert field not in readiness["payload"]["open_items"][0]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_blank_unauthorized_and_unknown_followup_details_cannot_close(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        variants = [{**item(), name: "  "} for name in ("item_id", "owner_ref", "trigger_or_due")]
        variants += [
            {**item(), "disposition": "ACCEPTED_RISK"},
            {**item(), "owner_ref": None, "followup_ref": "missing-followup"},
            {
                **item(),
                "residual_risk": {
                    "description": "Claimed known",
                    "state": "KNOWN",
                    "basis_refs": [],
                },
            },
        ]
        for ordinal, candidate in enumerate(variants):
            result = cast(
                dict[str, Any],
                value(
                    await runtime.bus.dispatch(
                        request(
                            "closure/readiness/assess",
                            f"bad-{ordinal}",
                            {
                                "project_id": "p",
                                "contract_version": 2,
                                "closure_scope": "THREAD_CYCLE",
                                "scope_ref": "t",
                                "disposition": "DEFERRED",
                                "open_items": [candidate],
                            },
                        )
                    )
                )["readiness"],
            )
            assert result["state"] == "NOT_READY" and result["payload"]["open_item_issues"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_v2_structural_error_has_no_readiness_write(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        incomplete = item()
        del incomplete["residual_risk"]
        response = await runtime.bus.dispatch(
            request(
                "closure/readiness/assess",
                "v2",
                {
                    "project_id": "p",
                    "contract_version": 2,
                    "closure_scope": "THREAD_CYCLE",
                    "scope_ref": "t",
                    "disposition": "DEFERRED",
                    "open_items": [incomplete],
                },
            )
        )
        assert response.error is not None and response.error.code == -32602
        assert not SqliteControlRecordStore(runtime.ledger.engine).list("p", "CLOSURE", "READINESS")
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_valid_deferred_item_and_followup_only_readback(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        created = value(
            await runtime.bus.dispatch(
                request(
                    "closure/readiness/assess",
                    "ready",
                    {
                        "project_id": "p",
                        "contract_version": 2,
                        "closure_scope": "THREAD_CYCLE",
                        "scope_ref": "t",
                        "disposition": "DEFERRED",
                        "open_items": [item()],
                    },
                )
            )
        )
        readiness = cast(dict[str, Any], created["readiness"])
        assert readiness["state"] == "READY_WITH_OPEN_ITEMS"
        decision = cast(
            dict[str, Any],
            value(
                await runtime.bus.dispatch(
                    request(
                        "closure/decide",
                        "decide",
                        {
                            "project_id": "p",
                            "readiness_id": readiness["record_id"],
                            "decision": "DECIDE",
                            "actor_ref": "human:local-user",
                            "reason": "Routine deferred followup",
                        },
                    )
                )
            )["closure"],
        )
        assert decision["payload"]["open_items"][0] == item()
        followup = cast(
            dict[str, Any],
            value(
                await runtime.bus.dispatch(
                    request(
                        "closure/followup/create",
                        "followup",
                        {
                            "project_id": "p",
                            "closure_id": decision["record_id"],
                            "purpose": "Collect next observation",
                            "owner_ref": "human:local-user",
                            "trigger_or_due": "next observation",
                        },
                    )
                )
            )["followup"],
        )
        owned = item()
        del owned["owner_ref"]
        owned["followup_ref"] = followup["record_id"]
        linked = cast(
            dict[str, Any],
            value(
                await runtime.bus.dispatch(
                    request(
                        "closure/readiness/assess",
                        "linked",
                        {
                            "project_id": "p",
                            "contract_version": 2,
                            "closure_scope": "THREAD_CYCLE",
                            "scope_ref": "t",
                            "disposition": "DEFERRED",
                            "open_items": [owned],
                        },
                    )
                )
            )["readiness"],
        )
        assert linked["state"] == "READY_WITH_OPEN_ITEMS"
        assert linked["payload"]["open_items"][0]["residual_risk"]["state"] == "UNKNOWN"
    finally:
        runtime.close()
