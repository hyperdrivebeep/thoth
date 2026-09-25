from pathlib import Path
from typing import Any

from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import setup

from thoth.apps.runtime import AppRuntime
from thoth.domain.canonical import head_set_digest


async def prepare_outcome(
    workspace: Path, *, create_series: bool = True, assess: bool = False
) -> tuple[AppRuntime, dict[str, Any]]:
    runtime, ctx = await setup(workspace)
    generated = value(
        await runtime.bus.dispatch(
            request(
                "action/generate",
                "outcome-actions",
                {
                    "project_id": ctx["project"],
                    "object_id": ctx["object"],
                    "decision_need": "Observe a controlled result",
                    "evidence_scope": ctx["spans"],
                },
            )
        )
    )
    action = generated["actions"][0]
    plan = value(
        await runtime.bus.dispatch(
            request(
                "action/plan/compose",
                "outcome-plan",
                {
                    "project_id": ctx["project"],
                    "object_id": ctx["object"],
                    "selected_action_refs": [action["action_id"]],
                    "step_candidates": [
                        {
                            "step_id": "step:observation",
                            "action_ref": action["action_id"],
                            "inputs": ctx["spans"],
                            "output_contract": {"type": "measurement"},
                            "preconditions": [],
                            "stop_conditions": ["result recorded"],
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
        )
    )["plan"]
    ctx.update(action=action, plan=plan)
    series_input: dict[str, object] = {
        "project_id": ctx["project"],
        "object_id": ctx["object"],
        "action_plan_revision_digest": plan["revision_digest"],
        "profile_ref": "EXPERIMENT_LEARNING_OUTCOME",
        "planned_execution_ref": None,
        "comparison_baseline_set_digest": head_set_digest(
            runtime.ledger.read_heads(ctx["project"])
        ),
        "assessment_windows": [
            {"assessment_phase": "INTERIM", "window": "controlled local observation"}
        ],
    }
    ctx["series_input"] = series_input
    if create_series:
        ctx["series"] = value(
            await runtime.bus.dispatch(request("outcome/series/create", "series", series_input))
        )["series"]
    if assess:
        linked = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/observation/link",
                    "link",
                    {
                        "project_id": ctx["project"],
                        "outcome_series_id": ctx["series"]["outcome_series_id"],
                        "assessment_phase": "INTERIM",
                        "observation_refs": ctx["spans"],
                        "completeness": "PARTIAL",
                        "evidence_refs": ctx["spans"],
                        "expected_series_revision": ctx["series"]["revision"],
                    },
                )
            )
        )["series"]
        assessed = value(
            await runtime.bus.dispatch(
                request(
                    "outcome/assess",
                    "assess",
                    {
                        "project_id": ctx["project"],
                        "outcome_series_id": linked["outcome_series_id"],
                        "assessment_phase": "INTERIM",
                        "profile_version": 1,
                        "observation_refs": ctx["spans"],
                        "comparator_refs": ctx["spans"],
                        "assumptions": [],
                        "expected_series_revision": linked["revision"],
                    },
                )
            )
        )
        ctx.update(series=assessed["series"], assessment=assessed["assessment"])
    return runtime, ctx
