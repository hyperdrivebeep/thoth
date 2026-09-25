"""Current source permissions govern memory made by the normal authenticated loop."""

from pathlib import Path

import pytest
from tests.integration.resource_scope_helpers import denial, scope_harness, value
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model, StaticModelResolver

from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["beta", "owner"])
async def test_normal_memory_is_hidden_until_all_sources_are_shared(
    tmp_path: Path, reader: str
) -> None:
    policy = ResourceScopePolicy(
        workstreams={
            "alpha": ResourceScopeTemplate(
                owner_kind="WORKSTREAM", owner_workstream="alpha", visibility="WORKSTREAM"
            )
        }
    )
    async with scope_harness(
        tmp_path, policy, model_resolver=StaticModelResolver(DynamicA02Model()), owner_admin=False
    ) as h:
        source = value(await h.connect("alpha", "memory-source", None))["artifact"]["artifact_id"]
        value(
            await h.call(
                "alpha",
                "thread/start",
                "memory-thread",
                {
                    "thread_id": "thread:alpha:memory",
                    "problem": "Which trial method should we compare?",
                    "scope": {"workstream": "alpha"},
                },
            )
        )
        cycle = value(
            await h.call(
                "alpha",
                "thread/input",
                "memory-cycle",
                {
                    "thread_id": "thread:alpha:memory",
                },
            )
        )
        assert cycle["full_project_memory"]["committed"], cycle
        plan = value(
            await h.call(
                "alpha",
                "action/plan/read",
                "outcome-plan",
                {
                    "plan_id": cycle["action_plan"]["plan_id"],
                },
            )
        )["plan"]
        profiles = value(await h.call("alpha", "outcome/profile/list", "outcome-profiles", {}))[
            "profiles"
        ]
        series = value(
            await h.call(
                "alpha",
                "outcome/series/create",
                "private-outcome-series",
                {
                    "object_id": plan["object_id"],
                    "action_plan_revision_digest": plan["revision_digest"],
                    "profile_ref": profiles[0]["profile_ref"],
                    "comparison_baseline_set_digest": "a" * 64,
                    "assessment_windows": [{"assessment_phase": "INTERIM"}],
                },
            )
        )["series"]
        hidden_series = value(
            await h.call(reader, "outcome/series/list", "private-series-list", {})
        )["series"]
        assert hidden_series == [], "Outcome series query leaked private research scope"
        receipt = h.runtime.ledger.read_receipts(h.project)[-1]
        hidden_receipts = value(await h.call(reader, "receipt/list", "private-receipts", {}))[
            "receipts"
        ]
        assert hidden_receipts == [], "receipt query leaked private research lineage"
        hidden_dag = value(await h.call(reader, "receipt/audit/read", "private-receipt-dag", {}))[
            "dag"
        ]
        assert hidden_dag["nodes"] == [], "receipt DAG leaked private research lineage"
        own = value(await h.call("alpha", "memory/list", "own-memories", {}))["full_memories"]
        assert own
        action_audit = value(await h.call("alpha", "action/audit/read", "own-action-audit", {}))[
            "records"
        ]
        assert action_audit
        hidden_audit = value(await h.call(reader, "action/audit/read", "private-action-audit", {}))[
            "records"
        ]
        assert hidden_audit == [], "action audit query leaked private source content"
        criteria = value(await h.call("alpha", "criteria/list", "own-criteria", {}))["criteria"]
        assert criteria
        hidden_criteria = value(await h.call(reader, "criteria/list", "private-criteria", {}))[
            "criteria"
        ]
        assert hidden_criteria == [], "criterion query leaked private source content"
        spans = value(await h.call("alpha", "evidence/list", "criterion-reference-spans", {}))[
            "spans"
        ]
        reference = value(
            await h.call(
                "alpha",
                "criteria/reference/generate",
                "criterion-reference",
                {
                    "criterion_id": criteria[0]["criterion_id"],
                    "expected_revision_digest": criteria[0]["revision_digest"],
                    "source_scope": [s["span_id"] for s in spans],
                    "scenarios": ["trial comparison"],
                },
            )
        )
        hidden_references = value(
            await h.call(reader, "criteria/reference/list", "private-references", {})
        )
        assert hidden_references["references"] == [], reference
        summary = value(await h.call("alpha", "hypothesis/list", "own-hypotheses", {}))[
            "hypotheses"
        ][0]
        hypothesis = value(
            await h.call(
                "alpha",
                "hypothesis/read",
                "own-hypothesis",
                {
                    "hypothesis_id": summary["hypothesis_id"],
                },
            )
        )["hypothesis"]
        denial(
            await h.call(
                reader,
                "revision/read",
                "private-revision-read",
                {
                    "revision_digest": hypothesis["revision_digest"],
                },
            ),
            "RESOURCE_ACCESS_DENIED",
        )
        revision = h.runtime.ledger.read_revision_by_digest(
            h.project, hypothesis["revision_digest"]
        )
        assert revision is not None
        snapshot = h.runtime.ledger.read_snapshot(revision.snapshot_id)
        assert snapshot is not None
        proposal = value(
            await h.call(
                "alpha",
                "revision/propose",
                "private-proposal",
                {
                    "aggregate_id": hypothesis["hypothesis_id"],
                    "aggregate_type": "HYPOTHESIS",
                    "parent_revision_digests": [hypothesis["revision_digest"]],
                    "candidate_content": snapshot.content,
                    "reason": "alpha-private interpretation draft",
                    "evidence_refs": hypothesis["evidence_refs"],
                    "actor_or_agent_ref": h.actors["alpha"],
                    "expected_head_digest": hypothesis["revision_digest"],
                },
            )
        )["proposal"]
        hidden_proposals = value(
            await h.call(reader, "revision/audit/read", "private-proposal-audit", {})
        )["control_records"]
        assert hidden_proposals == [], "revision proposal audit leaked private candidate content"
        hidden_content = await h.call(
            reader,
            "revision/content/read",
            "private-content",
            {
                "content_digest": snapshot.content_digest,
            },
        )
        assert "error" in hidden_content.json()
        value(
            await h.call(
                "alpha",
                "hypothesis/assumption/add",
                "private-assumption",
                {
                    "hypothesis_id": hypothesis["hypothesis_id"],
                    "expected_revision_digest": hypothesis["revision_digest"],
                    "statement": "alpha-private auxiliary interpretation",
                    "role": "measurement",
                    "evidence_refs": hypothesis["evidence_refs"],
                },
            )
        )
        assumptions = value(
            await h.call("alpha", "hypothesis/assumption/list", "own-assumptions", {})
        )["assumptions"]
        assert assumptions
        hidden_assumptions = value(
            await h.call(reader, "hypothesis/assumption/list", "private-assumptions", {})
        )["assumptions"]
        assert hidden_assumptions == [], "assumption query leaked private hypothesis content"
        before = value(await h.call(reader, "memory/list", "before-share", {}))["full_memories"]
        assert before == [], "private source memory leaked across workstreams"
        legacy = value(await h.call(reader, "memory/context/read", "legacy-before", {}))[
            "memory_context"
        ]
        assert not legacy["included_records"], legacy
        scope = value(
            await h.call(
                "alpha",
                "project/source/scope/read",
                "memory-scope",
                {
                    "resource_ref": source,
                },
            )
        )["scope"]
        shared = value(
            await h.call(
                "alpha",
                "project/source/scope/grant",
                "share-memory-source",
                {
                    "resource_ref": source,
                    "expected_revision": scope["revision"],
                    "grantee_kind": "ACTOR",
                    "grantee_ref": h.actors[reader],
                    "reason": "B source sharing",
                },
            )
        )["scope"]
        after = value(await h.call(reader, "memory/list", "after-share", {}))["full_memories"]
        assert {m["revision_digest"] for m in after} == {m["revision_digest"] for m in own}
        assert all(m["scope"]["workstream"] == "alpha" for m in after)
        shared_proposals = value(
            await h.call(reader, "revision/audit/read", "shared-proposal-audit", {})
        )["control_records"]
        assert any(p["record_digest"] == proposal["record_digest"] for p in shared_proposals)
        shared_dag = value(await h.call(reader, "receipt/audit/read", "shared-receipt-dag", {}))[
            "dag"
        ]
        assert shared_dag["nodes"]
        assert any(n["kind"] == "MODEL" for n in shared_dag["nodes"])
        shared_content = value(
            await h.call(
                reader,
                "revision/content/read",
                "shared-content",
                {
                    "content_digest": snapshot.content_digest,
                },
            )
        )["snapshot"]
        assert shared_content["content_digest"] == snapshot.content_digest
        shared_receipt = value(
            await h.call(
                reader,
                "receipt/read",
                "shared-receipt",
                {
                    "receipt_id": receipt.receipt_id,
                },
            )
        )["receipt"]
        assert shared_receipt["receipt_digest"] == receipt.receipt_digest
        shared_series = value(
            await h.call(
                reader,
                "outcome/series/read",
                "shared-series-read",
                {
                    "outcome_series_id": series["outcome_series_id"],
                },
            )
        )["series"]
        assert shared_series["revision_digest"] == series["revision_digest"]
        shared_audit = value(await h.call(reader, "action/audit/read", "shared-action-audit", {}))[
            "records"
        ]
        assert {a["event_digest"] for a in shared_audit} == {
            a["event_digest"] for a in action_audit
        }
        shared_criteria = value(await h.call(reader, "criteria/list", "shared-criteria", {}))[
            "criteria"
        ]
        assert {c["revision_digest"] for c in shared_criteria} == {
            c["revision_digest"] for c in criteria
        }
        shared_assumptions = value(
            await h.call(reader, "hypothesis/assumption/list", "shared-assumptions", {})
        )["assumptions"]
        assert {p["assumption_digest"] for p in shared_assumptions} == {
            p["assumption_digest"] for p in assumptions
        }
        value(
            await h.call(
                reader,
                "thread/start",
                "beta-memory-thread",
                {
                    "thread_id": "thread:beta:memory",
                    "problem": "Which trial method should we compare?",
                    "scope": {"workstream": reader},
                },
            )
        )
        followup = value(
            await h.call(
                reader,
                "thread/input",
                "beta-memory-cycle",
                {
                    "thread_id": "thread:beta:memory",
                },
            )
        )
        recalled = followup["full_project_memory_context"]["included"]
        assert recalled, "B must permit cross-workstream recall after source sharing"
        assert all(m["origin_thread_id"] == "thread:alpha:memory" for m in recalled)
        value(
            await h.call(
                "alpha",
                "project/source/scope/revoke",
                "revoke-memory-source",
                {
                    "resource_ref": source,
                    "expected_revision": shared["revision"],
                    "grant_id": shared["grants"][0]["grant_id"],
                    "reason": "withdraw shared source",
                },
            )
        )
        withdrawn = value(await h.call(reader, "memory/list", "after-revoke", {}))["full_memories"]
        assert withdrawn == []
