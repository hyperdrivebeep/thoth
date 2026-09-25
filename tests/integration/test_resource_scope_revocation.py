"""Revocation applies to fresh reads and previously successful operation results."""

from pathlib import Path
from typing import cast

import httpx
import pytest
from tests.integration.resource_scope_helpers import ScopeHarness, denial, scope_harness, value


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "surface",
    [
        "replay",
        "operation/read",
        "operation/result/read",
        "query:operation/read",
        "query:operation/result/read",
    ],
)
async def test_revoke_blocks_cached_resource_content(tmp_path: Path, surface: str) -> None:
    async with scope_harness(tmp_path) as h:
        connected = value(
            await h.connect(
                "alpha",
                "private-source",
                {
                    "owner_kind": "WORKSTREAM",
                    "owner_workstream": "alpha",
                    "visibility": "WORKSTREAM",
                },
            )
        )
        resource = connected["artifact"]["artifact_id"]
        scope = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "scope-before",
                {
                    "resource_ref": resource,
                },
            )
        )["scope"]
        spans = value(await h.call("alpha", "evidence/list", "private-spans", {}))["spans"]
        span = next(item for item in spans if "alpha-private-marker" in item["exact_text"])
        granted = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "grant-beta",
                {
                    "resource_ref": resource,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "bounded source review",
                },
            )
        )["scope"]
        first = await h.call("beta", "evidence/read", "beta-success", {"span_id": span["span_id"]})
        assert "alpha-private-marker" in str(value(first))
        operation_id = _text(_record(_record(first.json())["result"])["operation_id"])
        if surface.startswith("query:"):
            before_revoke = await historical_query(
                h, surface.removeprefix("query:"), "before-query", operation_id
            )
            assert "alpha-private-marker" in str(value(before_revoke))
        revoked = value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "revoke-beta",
                {
                    "resource_ref": resource,
                    "expected_revision": granted["revision"],
                    "grant_id": granted["grants"][0]["grant_id"],
                    "reason": "review access withdrawn",
                },
            )
        )["scope"]
        assert revoked["grants"][0]["state"] == "REVOKED"
        assert revoked["revision"] == granted["revision"] + 1
        denial(
            await h.call(
                "beta",
                "evidence/read",
                "beta-fresh-denied",
                {
                    "span_id": span["span_id"],
                },
            ),
            "RESOURCE_ACCESS_DENIED",
        )
        assert "alpha-private-marker" in str(
            value(
                await h.call(
                    "alpha",
                    "evidence/read",
                    "owner-still-reads",
                    {
                        "span_id": span["span_id"],
                    },
                )
            )
        )
        if surface == "replay":
            response = await h.call(
                "beta", "evidence/read", "beta-success", {"span_id": span["span_id"]}
            )
        elif surface.startswith("query:"):
            response = await historical_query(
                h, surface.removeprefix("query:"), "after-query", operation_id
            )
        else:
            response = await h.call(
                "beta", surface, f"cached-{surface}", {"operation_id": operation_id}
            )
        denial(response, "RESOURCE_ACCESS_DENIED")
        assert "alpha-private-marker" not in response.text


async def historical_query(
    h: ScopeHarness, method: str, key: str, operation_id: str
) -> httpx.Response:
    from tests.integration.storage_coverage_helpers import request

    payload = _record(request(
        method, key, {"project_id": h.project, "operation_id": operation_id}
    ).model_dump(mode="json", by_alias=True))
    _record(_record(payload["params"])["_meta"])["dataScope"] = {"workstream": "beta"}
    return await h.client.post(
        "/rpc/query", json=payload, headers={"authorization": f"Bearer {h.tokens['beta']}"}
    )
