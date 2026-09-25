from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime, fixture_scope_policy
from tests.integration.test_a02_autonomous_acquisition import StaticModelResolver, request, value
from tests.integration.test_a03_critical_counter_search import (
    A03Connector,
    CriticalA03Model,
)

from thoth.adapters.connectors import ConnectorRegistry
from thoth.ports.model import ModelPort

RouteSpec = tuple[str, str, str, bytes, int, str, list[str]]


def route(
    route_id: str,
    connector_id: str,
    relative_path: str,
    *,
    priority: int,
    authority: str = "OFFICIAL",
    context_tags: list[str] | None = None,
) -> dict[str, object]:
    return {
        "route_id": route_id,
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
        "selector": {"relative_path": relative_path},
        "query_families": [
            f"independent territory {route_id}",
            f"alternative explanation {route_id}",
            f"bounded excursion {route_id}",
        ],
        "source_territory": connector_id,
        "independence_group": f"territory:{connector_id}",
        "alternative_explanation": f"alternative mechanism for {route_id}",
        "source_authority": authority,
        "temporal_state": "ELIGIBLE",
        "support_match_terms": ["replicates dominant"],
        "counter_match_terms": ["falsifies dominant"],
        "context_tags": context_tags or [],
        "max_waves": 1,
        "max_results": 4,
        "priority": priority,
        "depth": 1,
        "excursion": route_id.endswith("excursion"),
        "checkpoint_after": True,
        "estimated_cost_microunits": 5,
    }


def policy(
    scenario: str,
    routes: list[dict[str, object]],
) -> dict[str, object]:
    connector_ids = [str(item["connector_id"]) for item in routes]
    allowlist = ["mw-primary"] if scenario == "policy" else ["mw-primary", *connector_ids]
    max_waves = 1 if scenario in {"budget", "same_source_spans"} else 6
    return {
        "resource_scope_policy": fixture_scope_policy().model_dump(mode="json"),
        "external_write": False,
        "physical_action": False,
        "unknown_action_tier": "R3",
        "connector_default": "DENY",
        "connector_allowlist": list(dict.fromkeys(allowlist)),
        "connector_allowed_egress_classes": ["NONE"],
        "max_source_security_class": "RESTRICTED",
        "sandbox_runtime_allowlist": [],
        "sandbox_network_policy": "DENY_ALL",
        "sandbox_allowed_hosts": [],
        "acquisition_routes": [],
        "counter_search_routes": routes,
        "counter_search_limits": {
            "max_waves": max_waves,
            "max_results": 20,
            "max_documents": 6,
            "max_bytes": 262144,
            "max_model_calls": 0,
            "max_tool_calls": 12,
            "max_time_seconds": 30,
            "max_cost_microunits": 100,
            "max_depth": 2,
            "min_independent_confirmations": 2,
            "saturation_voi_threshold": "0.10",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_terminal"),
    (
        ("converged", "ELIMINATED_WITHIN_SCOPE"),
        ("same_source_spans", "SEARCH_SATURATED"),
        ("saturated", "SEARCH_SATURATED"),
        ("budget", "BUDGET_EXHAUSTED"),
        ("policy", "POLICY_BLOCKED"),
        ("authority", "AUTHORITY_REQUIRED"),
        ("abstained", "ABSTAINED"),
    ),
)
async def test_thread_input_runs_bounded_multi_wave_counter_research(
    tmp_path: Path,
    scenario: str,
    expected_terminal: str,
) -> None:
    duplicate = b"# Independent review\n\nfalsifies dominant duplicate result\n"
    distinct = b"# Independent review two\n\nfalsifies dominant second confirmation\n"
    no_match = b"# Search result\n\nNo contract matching evidence.\n"
    support = b"# Independent support\n\nreplicates dominant explanation\n"
    primary = A03Connector(
        "mw-primary",
        {"primary.md": b"# Primary\n\nCurrent evidence supports the dominant explanation.\n"},
    )
    connectors = [primary]
    route_values: list[dict[str, object]] = []
    specs: tuple[RouteSpec, ...]
    if scenario == "converged":
        specs = (
            ("wave-high", "mw-one", "counter-one.md", duplicate, 100, "OFFICIAL", []),
            ("wave-duplicate", "mw-two", "counter-two.md", duplicate, 90, "OFFICIAL", []),
            ("wave-excursion", "mw-three", "counter-three.md", distinct, 80, "OFFICIAL", []),
        )
    elif scenario == "same_source_spans":
        specs = (
            (
                "wave-one-source",
                "mw-one",
                "counter-one.md",
                (
                    b"# One source\n\n"
                    b"falsifies dominant first matching span\n\n"
                    b"falsifies dominant second matching span\n"
                ),
                100,
                "OFFICIAL",
                [],
            ),
        )
    elif scenario in {"saturated", "budget", "policy"}:
        specs = (
            ("wave-one", "mw-one", "empty-one.md", no_match, 100, "OFFICIAL", []),
            ("wave-two", "mw-two", "empty-two.md", no_match, 90, "OFFICIAL", []),
            ("wave-three", "mw-three", "empty-three.md", no_match, 80, "OFFICIAL", []),
        )
    elif scenario == "authority":
        specs = (
            ("wave-one", "mw-one", "counter-one.md", duplicate, 100, "UNCLASSIFIED", []),
            ("wave-two", "mw-two", "counter-two.md", distinct, 90, "UNCLASSIFIED", []),
        )
    else:
        specs = (
            ("wave-counter", "mw-one", "counter.md", duplicate, 100, "OFFICIAL", []),
            ("wave-support", "mw-two", "support.md", support, 90, "OFFICIAL", []),
            (
                "wave-hidden",
                "mw-three",
                "hidden.md",
                distinct,
                80,
                "OFFICIAL",
                ["HIDDEN_HOLDOUT", "ORACLE", "CROSS_PROJECT"],
            ),
        )
    for route_id, connector_id, relative_path, payload, priority, authority, tags in specs:
        connectors.append(A03Connector(connector_id, {relative_path: payload}))
        route_values.append(
            route(
                route_id,
                connector_id,
                relative_path,
                priority=priority,
                authority=authority,
                context_tags=tags,
            )
        )
    workspace = tmp_path / scenario
    runtime = create_runtime(
        workspace,
        connector_registry=ConnectorRegistry(tuple(connectors)),
        model_resolver=StaticModelResolver(cast(ModelPort, CriticalA03Model())),
    )
    project_id = f"project:a03:multi:{scenario}"
    thread_id = f"thread:a03:multi:{scenario}"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    f"mw-{scenario}-create",
                    {
                        "project_id": project_id,
                        "name": f"Multi-wave {scenario}",
                        "cutoff_at": "2026-09-01T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/policy/update",
                    f"mw-{scenario}-policy",
                    {
                        "project_id": project_id,
                        "expected_revision": 0,
                        "payload": policy(scenario, route_values),
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    f"mw-{scenario}-primary",
                    {
                        "project_id": project_id,
                        "connector_id": "mw-primary",
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
                    f"mw-{scenario}-thread",
                    {
                        "project_id": project_id,
                        "thread_id": thread_id,
                        "problem": "Run bounded multi-wave independent counter research.",
                        "scope": {"workstream": "multi-wave-critical"},
                    },
                )
            )
        )
        analyzed = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    f"mw-{scenario}-input",
                    {"project_id": project_id, "thread_id": thread_id},
                )
            )
        )
        advanced = cast(dict[str, JsonValue], analyzed["critical_counter_search"])
        investigation = cast(dict[str, JsonValue], advanced["investigation"])
        audit = value(
            await runtime.bus.dispatch(
                request(
                    "investigation/audit/read",
                    f"mw-{scenario}-audit",
                    {
                        "project_id": project_id,
                        "investigation_id": investigation["investigation_id"],
                    },
                )
            )
        )
    finally:
        runtime.close()

    assert advanced["loop_terminal"] == expected_terminal
    waves = cast(list[dict[str, JsonValue]], advanced["waves"])
    budget = cast(dict[str, JsonValue], advanced["budget"])
    assert len(waves) <= int(str(budget["max_waves"]))
    assert int(str(budget["used_results"])) <= int(str(budget["max_results"]))
    assert int(str(budget["used_documents"])) <= int(str(budget["max_documents"]))
    assert int(str(budget["used_bytes"])) <= int(str(budget["max_bytes"]))
    assert int(str(budget["used_tool_calls"])) <= int(str(budget["max_tool_calls"]))
    assert int(str(budget["used_model_calls"])) <= int(str(budget["max_model_calls"]))
    assert int(str(budget["used_cost_microunits"])) <= int(str(budget["max_cost_microunits"]))
    records = cast(list[dict[str, JsonValue]], audit["records"])
    if scenario == "converged":
        assert len(waves) == 3
        assert sum(bool(wave["deduplicated"]) for wave in waves) == 1
        assert sum(not bool(wave["deduplicated"]) for wave in waves) == 2
        assert all(float(str(wave["computed_voi"])) >= 0 for wave in waves)
        assert [int(str(wave["priority"])) for wave in waves] == [100, 90, 80]
        assert bool(waves[-1]["excursion"]) is True
        challenger_plans = cast(list[dict[str, JsonValue]], advanced["challenger_plans"])
        assert all(len(cast(list[object], plan["tracks"])) == 2 for plan in challenger_plans)
        assert any(item["event_type"] == "investigation/checkpointCreated" for item in records)
        assert any(
            cast(dict[str, JsonValue], item["payload"]).get("reason") == "RESUMED"
            for item in records
            if item["event_type"] == "investigation/checkpointCreated"
        )
    elif scenario == "policy" or scenario == "authority":
        assert waves == []
    elif scenario == "abstained":
        assert cast(dict[str, list[str]], advanced["support_diff"])["added"] == []
        assert cast(dict[str, list[str]], advanced["counterevidence_diff"])["added"] == []
        hidden_connector = next(
            connector for connector in connectors if connector.capability.connector_id == "mw-three"
        )
        assert hidden_connector.fetch_count == 0
