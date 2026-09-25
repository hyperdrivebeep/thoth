from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.integration.resource_scope_helpers import denial, scope_harness, value
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model, StaticModelResolver

from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


async def test_reference_model_revocation_blocks_commit_and_cached_inquiry_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    async with scope_harness(
        tmp_path, policy, model_resolver=StaticModelResolver(DynamicA02Model())
    ) as h:
        source = value(await h.connect("alpha", "reference-source", None))["artifact"][
            "artifact_id"
        ]
        scope = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "scope",
                {
                    "resource_ref": source,
                },
            )
        )["scope"]
        shared = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "share",
                {
                    "resource_ref": source,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "Allow reference inquiry with source",
                },
            )
        )["scope"]
        thread = "thread:beta:reference"
        value(
            await h.call(
                "beta",
                "thread/start",
                "start",
                {
                    "thread_id": thread,
                    "problem": "Which trial method should we compare?",
                    "scope": {"workstream": "beta"},
                },
            )
        )
        spans = value(await h.call("beta", "evidence/list", "spans", {}))["spans"]
        refs = [s["span_id"] for s in spans]
        criterion = value(
            await h.call(
                "beta",
                "criteria/compile",
                "compile",
                {
                    "thread_id": thread,
                    "source_span_ids": refs,
                    "profile_refs": ["GENERAL_RND"],
                },
            )
        )["criterion"]
        first_payload: dict[str, object] = {
            "thread_id": thread,
            "reference_request": {
                "criterion_id": criterion["criterion_id"],
                "expected_revision_digest": criterion["revision_digest"],
                "lane": "REFERENCE_RANGE_CANDIDATE",
                "source_refs": refs,
                "calculator_id": "observed-range",
                "calculator_version": "1.0.0",
                "target": {},
                "measurements": [],
            },
        }
        first = value(await h.call("beta", "thread/input", "first-reference", first_payload))
        assert first["reference_inquiry"]["state"] == "NEEDS_INPUT"
        original = DynamicA02Model.structured
        calls: list[bool] = []

        async def withdraw[T: BaseModel](
            self: DynamicA02Model, request: ModelRequest[T]
        ) -> ModelResult[T]:
            if request.role != ModelRole.REFERENCE_MAPPER:
                return await original(self, request)
            calls.append(True)
            value(
                await h.call(
                    "alpha",
                    "project/source/scope/revoke",
                    "withdraw",
                    {
                        "resource_ref": source,
                        "expected_revision": shared["revision"],
                        "grant_id": shared["grants"][0]["grant_id"],
                        "reason": "Withdraw during mapping",
                    },
                )
            )
            output = request.output_model.model_validate({})
            return ModelResult(
                output=output,
                model_id="SCRIPTED_REVOKED_MAPPING",
                scripted=True,
                prompt_version=request.prompt_version,
                input_digest=domain_digest(
                    "MODEL_INPUT", "1.0.0", canonical_payload(request.context_pack)
                ),
                output_digest=domain_digest("MODEL_OUTPUT", "1.0.0", canonical_payload(output)),
            )

        monkeypatch.setattr(DynamicA02Model, "structured", withdraw)
        response = await h.call(
            "beta",
            "thread/input",
            "map-revoked",
            {
                "thread_id": thread,
                "instruction": "Continue checking the source conditions.",
            },
        )
        assert calls == [True]
        assert "error" in response.json(), response.text
        denial(
            await h.call("beta", "thread/input", "first-reference", first_payload),
            "RESOURCE_ACCESS_DENIED",
        )
        current = value(
            await h.call(
                "alpha",
                "criteria/read",
                "owner-read",
                {
                    "criterion_id": criterion["criterion_id"],
                },
            )
        )["criterion"]
        assert current["reference_inquiry"] == first["reference_inquiry"]
