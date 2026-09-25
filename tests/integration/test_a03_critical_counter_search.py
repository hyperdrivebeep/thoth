from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from sqlalchemy import func, select
from tests.integration.scoped_runtime import create_runtime, fixture_scope_policy
from tests.integration.test_a02_autonomous_acquisition import (
    DynamicA02Model,
    StaticModelResolver,
    request,
    value,
)
from tests.integration.test_a07_semantic_three_way_merge import commit

from thoth.adapters.connectors import ConnectorRegistry
from thoth.adapters.storage.schema import evidence_leads, source_bindings
from thoth.domain.connectors import (
    ConnectorAccessRequest,
    ConnectorArtifactRef,
    ConnectorCapability,
    ConnectorCheckpoint,
    ConnectorFetchResult,
    ConnectorOperation,
    ConnectorSelectorContract,
    NativeVersion,
    NativeVersionKind,
    SelectorFieldSpec,
)
from thoth.domain.enums import CausalLocus, RiskTier
from thoth.domain.hypothesis import DiscriminatingTest, Hypothesis
from thoth.ports.model import ModelPort


class CriticalA03Model(DynamicA02Model):
    @staticmethod
    def _hypothesis(
        hypothesis_id: str,
        object_id: str,
        locus: CausalLocus,
        span_id: str,
    ) -> Hypothesis:
        base = DynamicA02Model._hypothesis(
            hypothesis_id,
            object_id,
            locus,
            span_id,
        )
        tests = tuple(
            DiscriminatingTest(
                test_id=item.test_id,
                procedure_candidate=item.procedure_candidate,
                expected_if_true=item.expected_if_true,
                expected_if_alternative=item.expected_if_alternative,
                risk_tier=RiskTier.R2,
                reversibility=item.reversibility,
                estimated_cost=item.estimated_cost,
            )
            for item in base.discriminating_tests
        )
        return base.model_copy(update={"discriminating_tests": tests})


class A03Connector:
    def __init__(self, connector_id: str, payloads: dict[str, bytes]) -> None:
        self.capability = ConnectorCapability(
            connector_id=connector_id,
            source_kind="LOCAL",
            driver_version="a03-test-v1",
            operations=(ConnectorOperation.DISCOVER, ConnectorOperation.READ),
            native_version_kinds=(NativeVersionKind.CONTENT_HASH,),
            egress_class="NONE",
            selector_contract=ConnectorSelectorContract(
                fields=(SelectorFieldSpec(name="relative_path", value_type="STRING"),),
            ),
        )
        self.payloads = payloads
        self.discover_count = 0
        self.fetch_count = 0
        self.close_count = 0
        self.on_fetch: Callable[[], Awaitable[None]] | None = None

    async def discover(
        self,
        request: ConnectorAccessRequest,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> tuple[ConnectorArtifactRef, ...]:
        del checkpoint
        self.discover_count += 1
        name = str(request.selector["relative_path"])
        raw = self.payloads[name]
        digest = hashlib.sha256(raw).hexdigest()
        return (
            ConnectorArtifactRef(
                source_uri=f"a03://{self.capability.connector_id}/{name}",
                locator={"relative_path": name},
                media_type="text/markdown",
                native_version=NativeVersion(
                    kind=NativeVersionKind.CONTENT_HASH,
                    value=digest,
                ),
                size_hint=len(raw),
                observed_at=datetime(2026, 9, 1, tzinfo=UTC),
            ),
        )

    async def fetch(
        self,
        request: ConnectorAccessRequest,
        ref: ConnectorArtifactRef,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> ConnectorFetchResult:
        del checkpoint
        self.fetch_count += 1
        callback, self.on_fetch = self.on_fetch, None
        if callback is not None:
            await callback()
        name = str(request.selector["relative_path"])
        if name == "failure.md":
            raise RuntimeError("injected counter connector failure")
        raw = self.payloads[name]
        return ConnectorFetchResult(
            ref=ref,
            raw=raw,
            content_sha256=hashlib.sha256(raw).hexdigest(),
        )

    async def close(self, connector_run_id: str) -> None:
        del connector_run_id
        self.close_count += 1


def policy_payload(scenario: str) -> dict[str, object]:
    connector_id = "a03-primary" if scenario == "independence" else "a03-counter"
    allowlist = ["a03-primary"] if scenario == "policy" else ["a03-primary", "a03-counter"]
    source_authority = "UNCLASSIFIED" if scenario == "authority" else "OFFICIAL"
    temporal_state = "AFTER_CUTOFF" if scenario == "temporal" else "ELIGIBLE"
    context_tags = ["HIDDEN_HOLDOUT", "ORACLE"] if scenario == "forbidden" else []
    source_name = {
        "counter": "counter.md",
        "support": "support.md",
        "no_results": "empty.md",
        "failure": "failure.md",
    }.get(scenario, "counter.md")
    return {
        "external_write": False,
        "resource_scope_policy": fixture_scope_policy().model_dump(mode="json"),
        "physical_action": False,
        "unknown_action_tier": "R3",
        "connector_default": "DENY",
        "connector_allowlist": allowlist,
        "connector_allowed_egress_classes": ["NONE"],
        "max_source_security_class": "RESTRICTED",
        "sandbox_runtime_allowlist": [],
        "sandbox_network_policy": "DENY_ALL",
        "sandbox_allowed_hosts": [],
        "acquisition_routes": [],
        "counter_search_routes": [
            {
                "target_loci": [
                    "INPUT_MATERIAL_DATA",
                    "MEASUREMENT_OBSERVATION",
                    "METHOD_DESIGN_IMPLEMENTATION",
                    "COMPONENT_INTERFACE_SYSTEM",
                    "ENVIRONMENT_CONTEXT",
                    "HUMAN_ORGANIZATION_EXECUTION",
                    "OTHER_WITH_DESCRIPTION",
                ],
                "connector_id": connector_id,
                "selector": {"relative_path": source_name},
                "query_families": [
                    "independent source territory",
                    "alternative explanation",
                ],
                "source_territory": connector_id,
                "independence_group": f"territory:{connector_id}",
                "alternative_explanation": "the reported effect is caused by a registry mismatch",
                "source_authority": source_authority,
                "temporal_state": temporal_state,
                "support_match_terms": ["replicates dominant"],
                "counter_match_terms": ["falsifies dominant"],
                "context_tags": context_tags,
                "max_waves": 1,
                "max_results": 3,
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_terminal", "counter_io"),
    (
        ("counter", "ELIMINATED_WITHIN_SCOPE", True),
        ("support", "SUPPORTED", True),
        ("stale_head", "UNRESOLVED_CONFLICT", True),
        ("policy", "UNRESOLVED_POLICY_BLOCKED", False),
        ("independence", "UNRESOLVED_INDEPENDENCE", False),
        ("authority", "UNRESOLVED_AUTHORITY", False),
        ("temporal", "UNRESOLVED_TEMPORAL", False),
        ("forbidden", "UNRESOLVED_PROHIBITED_CONTEXT", False),
        ("no_results", "UNRESOLVED_NO_RESULTS", True),
        ("failure", "UNRESOLVED_FAILED", True),
    ),
)
async def test_thread_input_runs_typed_critical_counter_search(
    tmp_path: Path,
    scenario: str,
    expected_terminal: str,
    counter_io: bool,
) -> None:
    primary = A03Connector(
        "a03-primary",
        {
            "primary.md": (
                b"# Primary report\n\nThe current analysis supports the dominant explanation.\n"
            ),
            "counter.md": b"# Same territory\n\nfalsifies dominant\n",
        },
    )
    counter = A03Connector(
        "a03-counter",
        {
            "counter.md": b"# Independent review\n\nfalsifies dominant within tested scope\n",
            "support.md": b"# Independent replication\n\nreplicates dominant explanation\n",
            "empty.md": b"# Independent search\n\nNo contract-matching result was found.\n",
            "failure.md": b"unused",
        },
    )
    workspace = tmp_path / scenario
    runtime = create_runtime(
        workspace,
        connector_registry=ConnectorRegistry((primary, counter)),
        model_resolver=StaticModelResolver(cast(ModelPort, CriticalA03Model())),
    )
    project_id = f"project:a03:{scenario}"
    thread_id = f"thread:a03:{scenario}"
    concurrent_head: list[str] = []
    lead_counts: list[int] = []
    detached_bindings: list[int] = []
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    f"a03-{scenario}-create",
                    {
                        "project_id": project_id,
                        "name": f"A03 {scenario}",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    f"a03-{scenario}-policy",
                    {
                        "project_id": project_id,
                        "expected_revision": 0,
                        "payload": policy_payload(scenario),
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    f"a03-{scenario}-primary",
                    {
                        "project_id": project_id,
                        "connector_id": "a03-primary",
                        "selector": {"relative_path": "primary.md"},
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
                    f"a03-{scenario}-thread",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "problem": (
                            "Is the current dominant hypothesis robust to independent challenge?"
                        ),
                        "scope": {"workstream": "critical-review"},
                    },
                )
            )
        )
        if scenario == "stale_head":

            async def advance_portfolio_head() -> None:
                heads = dict(runtime.ledger.read_heads(project_id))
                listed = value(
                    await runtime.bus.dispatch(
                        request(
                            "hypothesis/portfolio/list",
                            "a03-current-portfolios",
                            {"project_id": project_id},
                        )
                    )
                )
                portfolios = cast(list[dict[str, JsonValue]], listed["portfolios"])
                assert len(portfolios) == 1
                portfolio_id = str(portfolios[0]["portfolio_id"])
                head_key = f"HYPOTHESIS:{portfolio_id}"
                parent = heads[head_key]
                revision = runtime.ledger.read_revision_by_digest(project_id, parent)
                assert revision is not None
                snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
                assert snapshot is not None
                content = cast(dict[str, JsonValue], snapshot.content)
                proposed = value(
                    await runtime.bus.dispatch(
                        request(
                            "revision/propose",
                            "a03-stale-propose",
                            {
                                "project_id": project_id,
                                "aggregate_id": portfolio_id,
                                "aggregate_type": "HYPOTHESIS",
                                "parent_revision_digests": [parent],
                                "candidate_content": {
                                    **content,
                                    "quality_gaps": ["concurrent authorized update"],
                                },
                                "reason": "concurrent authorized A03 update",
                                "evidence_refs": [],
                                "actor_or_agent_ref": "agent:a03:concurrent",
                                "expected_head_digest": parent,
                                "candidate_schema_version": "1.0.0",
                            },
                        )
                    )
                )
                proposal = cast(dict[str, JsonValue], proposed["proposal"])
                committed = await commit(
                    runtime,
                    project_id=project_id,
                    expected_heads=heads,
                    proposal_digest=str(proposal["record_digest"]),
                    suffix="a03-stale",
                )
                head_set = cast(dict[str, str], committed["new_project_head_set"])
                concurrent_head.append(head_set[head_key])

            counter.on_fetch = advance_portfolio_head
            with runtime.ledger.engine.connect() as connection:
                lead_counts.append(
                    int(
                        connection.execute(
                            select(func.count()).select_from(evidence_leads)
                        ).scalar_one()
                    )
                )
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    f"a03-{scenario}-input",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        critical = cast(dict[str, JsonValue], analyzed["critical_counter_search"])
        investigation = cast(dict[str, JsonValue], critical["investigation"])
        portfolio_id = str(critical["portfolio_id"])
        revisions = runtime.ledger.read_revisions(project_id, "HYPOTHESIS", portfolio_id)
        if scenario == "stale_head":
            with runtime.ledger.engine.connect() as connection:
                lead_counts.append(
                    int(
                        connection.execute(
                            select(func.count()).select_from(evidence_leads)
                        ).scalar_one()
                    )
                )
                detached_bindings.append(
                    int(
                        connection.execute(
                            select(func.count())
                            .select_from(source_bindings)
                            .where(source_bindings.c.state == "DETACHED")
                        ).scalar_one()
                    )
                )
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/audit/read",
                    f"a03-{scenario}-audit",
                    {
                        "project_id": project_id,
                        "investigation_id": investigation["investigation_id"],
                    },
                )
            )
        )
    finally:
        runtime.close()

    assert critical["terminal_state"] == expected_terminal
    assert investigation["mode"] == "CRITICAL"
    assert (
        cast(dict[str, JsonValue], critical["challenger_plan"])["challenger_version"]
        == "counterevidence-challenger:1.0.0"
    )
    assert (
        cast(dict[str, JsonValue], critical["gate_review"])["reviewer_version"]
        == "independent-gate-reviewer:1.0.0"
    )
    challenger_plan = cast(dict[str, JsonValue], critical["challenger_plan"])
    tracks = cast(list[dict[str, JsonValue]], challenger_plan["tracks"])
    assert {str(track["kind"]) for track in tracks} == {
        "INDEPENDENT_SOURCE",
        "ALTERNATIVE_EXPLANATION",
    }
    before = cast(dict[str, JsonValue], critical["hypothesis_before"])
    after = cast(dict[str, JsonValue], critical["hypothesis_after"])
    assert before["hypothesis_id"] == after["hypothesis_id"]
    assert (
        critical["portfolio_id"]
        == cast(dict[str, JsonValue], critical["portfolio"])["portfolio_id"]
    )
    receipt = cast(dict[str, JsonValue], critical["non_truth_receipt"])
    assert receipt["semantic_truth_certified"] is False
    assert len(revisions) == (3 if scenario == "stale_head" else 2)
    records = cast(list[dict[str, JsonValue]], audit["records"])
    assert any(item["event_type"] == "investigation/counterSearchPlanned" for item in records)
    assert any(item["event_type"] == "investigation/independentGateReviewed" for item in records)
    assert (counter.fetch_count > 0) is counter_io
    gate_review = cast(dict[str, JsonValue], critical["gate_review"])
    if scenario in {"counter", "support", "no_results"}:
        assert len(cast(list[str], gate_review["executed_track_ids"])) == 2
    else:
        assert cast(list[str], gate_review["executed_track_ids"]) == []
    if scenario == "counter":
        reopened = create_runtime(workspace)
        try:
            reopened_revisions = reopened.ledger.read_revisions(
                project_id,
                "HYPOTHESIS",
                portfolio_id,
            )
            reopened_audit = value(
                await reopened.bus.dispatch(
                    request(
                        "investigation/audit/read",
                        "a03-counter-reopen-audit",
                        {
                            "project_id": project_id,
                            "investigation_id": investigation["investigation_id"],
                        },
                    )
                )
            )
        finally:
            reopened.close()
        assert len(reopened_revisions) == 2
        assert len(cast(list[object], reopened_audit["records"])) >= 3
    if scenario == "counter":
        assert cast(dict[str, list[str]], critical["counterevidence_diff"])["added"]
        assert after["status"] == "ELIMINATED_WITHIN_SCOPE"
    elif scenario == "support":
        assert cast(dict[str, list[str]], critical["support_diff"])["added"]
        assert after["status"] == "COUNTEREVIDENCE_CHECKED"
    else:
        assert cast(dict[str, list[str]], critical["counterevidence_diff"])["added"] == []
    if scenario == "stale_head":
        assert concurrent_head
        assert lead_counts == [0, 0]
        assert detached_bindings == [1]
        head_key = f"HYPOTHESIS:{portfolio_id}"
        assert runtime.ledger.read_heads(project_id)[head_key] == concurrent_head[0]
        current = runtime.ledger.read_revision_by_digest(project_id, concurrent_head[0])
        assert current is not None
        snapshot = runtime.ledger.read_snapshot(current.snapshot_id)
        assert snapshot is not None
        assert snapshot.content["quality_gaps"] == ["concurrent authorized update"]
