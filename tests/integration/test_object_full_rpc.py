from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime

from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, JsonValue], child)


@pytest.mark.asyncio
async def test_decision_object_profile_relation_attention_and_proposals(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "evidence.md").write_text(
        "# Integration evidence\n\nThe interface timestamp is inconsistent.\n",
        encoding="utf-8",
    )
    runtime = create_runtime(workspace)
    project_id = "project:object-full"
    thread_id = "thread:object-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "object-project",
                    {
                        "project_id": project_id,
                        "name": "Object full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "object-source",
                    {
                        "project_id": project_id,
                        "relative_path": "evidence.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        evidence = value(
            await runtime.bus.dispatch(
                request("evidence/list", "object-evidence", {"project_id": project_id})
            )
        )
        spans = cast(list[dict[str, JsonValue]], evidence["spans"])
        evidence_refs = [str(item["span_id"]) for item in spans]
        assert evidence_refs
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "object-thread",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "problem": "Why is the interface timestamp inconsistent?",
                        "scope": {"workstream": "integration"},
                    },
                )
            )
        )
        object_ids = cast(list[str], started["current_object_ids"])
        assert len(object_ids) == 1
        object_id = object_ids[0]

        objects = value(
            await runtime.bus.dispatch(
                request("object/list", "object-list", {"project_id": project_id})
            )
        )
        assert len(cast(list[object], objects["objects"])) == 1
        current = cast(
            dict[str, JsonValue],
            value(
                await runtime.bus.dispatch(
                    request(
                        "object/read",
                        "object-read",
                        {"project_id": project_id, "object_id": object_id},
                    )
                )
            )["object"],
        )
        assert current["profile_state"] == "MATCHED"

        profiles = value(
            await runtime.bus.dispatch(
                request("object/profile/list", "object-profiles", {"project_id": project_id})
            )
        )
        assert len(cast(list[object], profiles["profiles"])) == 3
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "object/profile/read",
                        "object-profile-read",
                        {
                            "project_id": project_id,
                            "profile_ref": "SYSTEMS_INTEGRATION",
                        },
                    )
                )
            )["profile"],
            dict,
        )

        candidates = value(
            await runtime.bus.dispatch(
                request(
                    "object/candidate/list",
                    "object-candidates",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        candidate_items = cast(list[dict[str, JsonValue]], candidates["candidates"])
        assert len(candidate_items) == 1
        candidate_id = str(candidate_items[0]["candidate_id"])
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "object/candidate/read",
                        "object-candidate-read",
                        {"project_id": project_id, "candidate_id": candidate_id},
                    )
                )
            )["candidate"],
            dict,
        )

        duplicate = value(
            await runtime.bus.dispatch(
                request(
                    "object/materialize",
                    "object-duplicate",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "purpose_statement": current["purpose_statement"],
                        "problem_frame": current["problem_frame"],
                        "focus_refs": ["workstream:integration"],
                        "trigger_evidence_refs": evidence_refs,
                        "profile_refs": ["GENERAL_RND_DECISION"],
                    },
                )
            )
        )
        assert duplicate["object"] is None
        duplicate_candidate = cast(dict[str, JsonValue], duplicate["candidate"])
        assert duplicate_candidate["permitted_transition"] == "HOLD_DUPLICATE"

        profiled = value(
            await runtime.bus.dispatch(
                request(
                    "object/profile/apply",
                    "object-profile-apply",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "profile_refs": ["SYSTEMS_INTEGRATION"],
                        "evidence_refs": evidence_refs,
                        "reason": "integration profile is applicable",
                    },
                )
            )
        )
        current = cast(dict[str, JsonValue], profiled["object"])
        facets = value(
            await runtime.bus.dispatch(
                request(
                    "object/facet/update",
                    "object-facets",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "add": ["TECHNICAL", "ARBITRARY_EXTENSION"],
                        "remove": [],
                        "evidence_refs": evidence_refs,
                        "reason": "classify the active case facets",
                    },
                )
            )
        )
        assert facets["accepted_facets"] == ["TECHNICAL"]
        assert facets["rejected_extensions"] == ["ARBITRARY_EXTENSION"]
        current = cast(dict[str, JsonValue], facets["object"])

        related = value(
            await runtime.bus.dispatch(
                request(
                    "object/relation/add",
                    "object-relation-add",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "relation_type": "DEPENDS_ON",
                        "target_ref": "CRITERION:latency",
                        "semantic_role": "latency criterion gates resolution",
                        "evidence_refs": evidence_refs,
                        "authority_state": "OFFICIAL",
                    },
                )
            )
        )
        relation = cast(dict[str, JsonValue], related["relation"])
        relation_id = str(relation["relation_id"])
        current = cast(dict[str, JsonValue], related["object"])
        relation_list = value(
            await runtime.bus.dispatch(
                request(
                    "object/relation/list",
                    "object-relation-list",
                    {"project_id": project_id, "object_id": object_id},
                )
            )
        )
        assert len(cast(list[object], relation_list["relations"])) == 1
        impact = value(
            await runtime.bus.dispatch(
                request(
                    "object/impact/read",
                    "object-impact",
                    {"project_id": project_id, "object_id": object_id},
                )
            )
        )
        impact_value = cast(dict[str, JsonValue], impact["impact"])
        assert "CRITERION:latency" in cast(list[str], impact_value["transitive_affected_refs"])

        removed = value(
            await runtime.bus.dispatch(
                request(
                    "object/relation/remove",
                    "object-relation-remove",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "relation_id": relation_id,
                        "reason": "criterion binding was superseded",
                        "evidence_refs": evidence_refs,
                    },
                )
            )
        )
        ended = cast(dict[str, JsonValue], removed["relation"])
        assert ended["active"] is False
        current = cast(dict[str, JsonValue], removed["object"])

        missing_scope = value(
            await runtime.bus.dispatch(
                request(
                    "object/frame/revise",
                    "object-remove-scope",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "frame_patch": {"workstream_refs": []},
                        "evidence_refs": evidence_refs,
                        "reason": "exercise deterministic profile blocker",
                    },
                )
            )
        )
        current = cast(dict[str, JsonValue], missing_scope["object"])
        held = value(
            await runtime.bus.dispatch(
                request(
                    "object/revalidate",
                    "object-revalidate-held",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "revision_digest": current["revision_digest"],
                        "trigger_reason": "profile changed",
                    },
                )
            )
        )
        current = cast(dict[str, JsonValue], held["object"])
        assert "MISSING:workstream_refs" in cast(list[str], current["blockers"])
        attention_items = cast(
            list[dict[str, JsonValue]],
            value(
                await runtime.bus.dispatch(
                    request(
                        "object/attention/list",
                        "object-attention-list",
                        {"project_id": project_id},
                    )
                )
            )["attention_items"],
        )
        attention_id = str(attention_items[0]["attention_id"])
        acknowledged = value(
            await runtime.bus.dispatch(
                request(
                    "object/attention/acknowledge",
                    "object-attention-ack",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "attention_id": attention_id,
                        "actor_ref": "human:integrator",
                        "note": "acknowledged but not yet resolved",
                    },
                )
            )
        )
        attention = cast(dict[str, JsonValue], acknowledged["attention"])
        assert attention["state"] == "ACKNOWLEDGED"
        current = cast(dict[str, JsonValue], acknowledged["object"])

        restored_scope = value(
            await runtime.bus.dispatch(
                request(
                    "object/frame/revise",
                    "object-restore-scope",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "frame_patch": {"workstream_refs": ["integration"]},
                        "evidence_refs": evidence_refs,
                        "reason": "restore the missing profile field",
                    },
                )
            )
        )
        current = cast(dict[str, JsonValue], restored_scope["object"])
        valid = value(
            await runtime.bus.dispatch(
                request(
                    "object/revalidate",
                    "object-revalidate-valid",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "revision_digest": current["revision_digest"],
                        "trigger_reason": "scope restored",
                    },
                )
            )
        )
        current = cast(dict[str, JsonValue], valid["object"])
        cleared = value(
            await runtime.bus.dispatch(
                request(
                    "object/attention/acknowledge",
                    "object-attention-clear",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "attention_id": attention_id,
                        "actor_ref": "human:integrator",
                        "note": "deterministic condition is resolved",
                    },
                )
            )
        )
        cleared_attention = cast(dict[str, JsonValue], cleared["attention"])
        assert cleared_attention["state"] == "CLEARED"
        current = cast(dict[str, JsonValue], cleared["object"])

        replanned = value(
            await runtime.bus.dispatch(
                request(
                    "object/work/replan",
                    "object-replan",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "revision_digest": current["revision_digest"],
                        "trigger_refs": evidence_refs,
                    },
                )
            )
        )
        assert replanned["proposed_active_work_mode"] == "INVESTIGATING"
        current = cast(dict[str, JsonValue], replanned["object"])

        split = value(
            await runtime.bus.dispatch(
                request(
                    "object/split/propose",
                    "object-split",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "partitions": [
                            {"name": "timestamp source"},
                            {"name": "interface configuration"},
                        ],
                        "evidence_refs": evidence_refs,
                        "rationale": "separate discriminating causal questions",
                    },
                )
            )
        )
        split_proposal = cast(dict[str, JsonValue], split["split_proposal"])
        assert split_proposal["applied"] is False

        followup = value(
            await runtime.bus.dispatch(
                request(
                    "object/followup/create",
                    "object-followup",
                    {
                        "project_id": project_id,
                        "object_id": object_id,
                        "expected_revision_digest": current["revision_digest"],
                        "purpose_statement": "Verify timestamp source independently",
                        "trigger_refs": evidence_refs,
                        "inherit_scope": True,
                    },
                )
            )
        )
        child = cast(dict[str, JsonValue], followup["object"])
        child_id = str(child["object_id"])
        assert child["parent_object_id"] == object_id

        merged = value(
            await runtime.bus.dispatch(
                request(
                    "object/merge/propose",
                    "object-merge",
                    {
                        "project_id": project_id,
                        "object_ids": [object_id, child_id],
                        "field_mapping": {"purpose_statement": "retain-both"},
                        "evidence_refs": evidence_refs,
                        "rationale": "preview only; semantic similarity cannot merge",
                        "expected_revision_digests": [
                            current["revision_digest"],
                            child["revision_digest"],
                        ],
                    },
                )
            )
        )
        merge_proposal = cast(dict[str, JsonValue], merged["merge_proposal"])
        assert merge_proposal["applied"] is False

        audit = value(
            await runtime.bus.dispatch(
                request(
                    "object/audit/read",
                    "object-audit",
                    {"project_id": project_id, "object_id": object_id},
                )
            )
        )
        assert len(cast(list[object], audit["records"])) >= 12
    finally:
        runtime.close()
