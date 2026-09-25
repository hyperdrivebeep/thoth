"""Revocation during a model call stops the normal loop before its next model input."""

from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.integration.resource_scope_helpers import scope_harness, value
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model, StaticModelResolver

from thoth.adapters.memory import DeterministicRoleMemoryReviewer
from thoth.domain.memory import MemoryReviewContext, MemoryRoleReview
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["MODEL", "MEMORY"])
async def test_revoke_during_first_model_call_prevents_second_provider_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    policy = ResourceScopePolicy(
        workstreams={
            "alpha": ResourceScopeTemplate(
                owner_kind="WORKSTREAM",
                owner_workstream="alpha",
                visibility="WORKSTREAM",
            )
        }
    )
    model = DynamicA02Model()
    original = DynamicA02Model.structured
    calls: list[str] = []
    async with scope_harness(tmp_path, policy, model_resolver=StaticModelResolver(model)) as h:
        source = value(await h.connect("alpha", "model-source", None))["artifact"]["artifact_id"]
        scope = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "model-scope",
                {
                    "resource_ref": source,
                },
            )
        )["scope"]
        shared = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "model-grant",
                {
                    "resource_ref": source,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "allow model input",
                },
            )
        )["scope"]

        async def revoke_once[T: BaseModel](
            self: DynamicA02Model,
            request: ModelRequest[T],
        ) -> ModelResult[T]:
            calls.append(request.role.value)
            if len(calls) == 1:
                value(
                    await h.call(
                        "alpha",
                        "project/source/scope/revoke",
                        "model-revoke",
                        {
                            "resource_ref": source,
                            "expected_revision": shared["revision"],
                            "grant_id": shared["grants"][0]["grant_id"],
                            "reason": "withdraw during first I/O",
                        },
                    )
                )
            return await original(self, request)

        review_original = DeterministicRoleMemoryReviewer.review

        async def revoke_review_once(
            self: DeterministicRoleMemoryReviewer,
            context: MemoryReviewContext,
        ) -> MemoryRoleReview:
            calls.append(context.role.value)
            if len(calls) == 1:
                value(
                    await h.call(
                        "alpha",
                        "project/source/scope/revoke",
                        "review-revoke",
                        {
                            "resource_ref": source,
                            "expected_revision": shared["revision"],
                            "grant_id": shared["grants"][0]["grant_id"],
                            "reason": "withdraw during review",
                        },
                    )
                )
            return await review_original(self, context)

        if stage == "MODEL":
            monkeypatch.setattr(DynamicA02Model, "structured", revoke_once)
        else:
            monkeypatch.setattr(DeterministicRoleMemoryReviewer, "review", revoke_review_once)
        value(
            await h.call(
                "beta",
                "thread/start",
                "model-thread",
                {
                    "thread_id": "thread:beta:model",
                    "problem": "Which trial method should we compare?",
                    "scope": {"workstream": "beta"},
                },
            )
        )
        response = await h.call(
            "beta",
            "thread/input",
            "model-cycle",
            {
                "thread_id": "thread:beta:model",
            },
        )
        assert len(calls) == 1, (
            "a later provider received context after its source grant was revoked"
        )
        assert "error" in response.json(), response.text
