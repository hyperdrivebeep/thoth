"""Public execution views recheck the source-bound plan used by the real attempt."""

from pathlib import Path

from tests.integration.resource_scope_helpers import scope_harness, value

from thoth.domain.canonical import head_set_digest
from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


async def test_execution_views_follow_current_plan_source_access(tmp_path: Path) -> None:
    policy = ResourceScopePolicy(
        workstreams={
            "alpha": ResourceScopeTemplate(
                owner_kind="WORKSTREAM",
                owner_workstream="alpha",
                visibility="WORKSTREAM",
            )
        }
    )
    async with scope_harness(tmp_path, policy) as h:
        resource = value(await h.connect("alpha", "execution-source", None))["artifact"][
            "artifact_id"
        ]
        spans = value(await h.call("alpha", "evidence/list", "spans", {}))["spans"]
        refs = [span["span_id"] for span in spans]
        thread = value(
            await h.call(
                "alpha",
                "thread/start",
                "thread",
                {
                    "thread_id": "thread:scope-execution",
                    "problem": "Read bounded target state",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        object_id = thread["current_object_ids"][0]
        action = value(
            await h.call(
                "alpha",
                "action/create",
                "read-action",
                {
                    "object_id": object_id,
                    "primary_purpose": "INFORMATION_ACQUISITION",
                    "specification": {
                        "description": "Read local target state",
                        "expected_observation_or_change": {"description": "target state observed"},
                        "effect_completeness_confirmed": True,
                        "stop_conditions": ["state read"],
                        "observability": "read receipt",
                        "effect_vector": {
                            "effect_completeness_confirmed": True,
                            "external_write": False,
                        },
                    },
                    "evidence_refs": refs,
                },
            )
        )["action"]
        plan = value(
            await h.call(
                "alpha",
                "action/plan/compose",
                "execution-plan",
                {
                    "object_id": object_id,
                    "plan_id": "plan:scope-execution",
                    "selected_action_refs": [action["action_id"]],
                    "step_candidates": [
                        {
                            "step_id": "step:read",
                            "action_ref": action["action_id"],
                            "inputs": refs,
                            "output_contract": {"type": "observation"},
                            "preconditions": [],
                            "stop_conditions": ["state read"],
                            "effect_vector": {
                                "effect_completeness_confirmed": True,
                                "external_write": False,
                            },
                            "state": "READY",
                        }
                    ],
                    "dependency_edges": [],
                },
            )
        )["plan"]
        value(
            await h.call(
                "alpha",
                "execution/start",
                "execution-start",
                {
                    "plan_id": plan["plan_id"],
                    "plan_revision_digest": plan["revision_digest"],
                    "execution_profile_ref": "execution:local-coordinator-v1",
                    "expected_working_head_digest": head_set_digest(
                        dict(h.runtime.ledger.read_heads(h.project))
                    ),
                },
            )
        )
        executions = value(await h.call("alpha", "execution/list", "executions", {}))["executions"]
        assert executions
        execution_id = executions[0]["plan_execution_id"]
        own = value(
            await h.call(
                "alpha", "execution/read", "own-execution", {"plan_execution_id": execution_id}
            )
        )
        assert own["attempts"]
        attempt_id = own["attempts"][0]["attempt_id"]
        queries = [
            ("execution/read", {"plan_execution_id": execution_id}),
            ("execution/attempt/read", {"attempt_id": attempt_id}),
            ("execution/effect/read", {"attempt_id": attempt_id}),
            ("execution/audit/read", {"plan_execution_id": execution_id}),
        ]
        for index, (method, fields) in enumerate(queries):
            response = await h.call("beta", method, f"before-{index}", fields)
            assert "error" in response.json(), response.json()
        assert not value(await h.call("beta", "execution/list", "before-list", {}))["executions"]
        current = value(
            await h.call("alpha", "project/source/scope/read", "scope", {"resource_ref": resource})
        )["scope"]
        granted = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "grant",
                {
                    "resource_ref": resource,
                    "expected_revision": current["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors["beta"],
                    "reason": "review execution",
                },
            )
        )["scope"]
        for index, (method, fields) in enumerate(queries):
            value(await h.call("beta", method, f"shared-{index}", fields))
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "revoke",
                {
                    "resource_ref": resource,
                    "expected_revision": granted["revision"],
                    "grant_id": granted["grants"][0]["grant_id"],
                    "reason": "review ended",
                },
            )
        )
        for index, (method, fields) in enumerate(queries):
            assert "error" in (await h.call("beta", method, f"revoked-{index}", fields)).json()
        assert not value(await h.call("beta", "execution/list", "revoked-list", {}))["executions"]
