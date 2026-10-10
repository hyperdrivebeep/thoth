"""A trace row starts an investigation with the same measure under the other conditions beside it.

The siblings are found from the stored trace's links and rules (the criteria of the same
requirement with the same measure and another condition), never from names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_trace_origin import Rpc, opened, origin_for, with_origin

from thoth.application.services.trace_csv import export_csv
from thoth.domain.trace_origin import (
    MAX_COMPARISON_REFS,
    MAX_SIBLING_SPAN_REFS,
    MAX_SIBLING_VERDICTS,
)
from thoth.domain.verification_trace import (
    Comparator,
    CriterionRule,
    ResultRecord,
    TraceItem,
    TraceKind,
    TraceLink,
    TraceRelation,
    TraceSet,
)

DRY, FOG, RAIN = "SYN-C-DET-DRY", "SYN-C-DET-FOG", "SYN-C-DET-RAIN"
FA_DRY = "SYN-C-FA-DRY"
T0 = datetime(2026, 10, 2, tzinfo=UTC)


def criterion_set(
    requirements: dict[str, list[tuple[str, str, str, int]]],
) -> TraceSet:
    """requirement -> [(criterion, measure, condition, number of result positions)].

    A criterion with positions also has one result (value 0.95, 19/20) pointing at them.
    """
    items: list[TraceItem] = []
    links: list[TraceLink] = []
    rules: list[CriterionRule] = []
    results: list[ResultRecord] = []
    for requirement, criteria in requirements.items():
        items.append(
            TraceItem(item_id=requirement, item_key=f"k-{requirement}", kind=TraceKind.REQUIREMENT)
        )
        for criterion, measure, condition, positions in criteria:
            items.append(
                TraceItem(item_id=criterion, item_key=f"k-{criterion}", kind=TraceKind.CRITERION)
            )
            links.append(
                TraceLink(
                    link_id=f"L-{criterion}",
                    from_id=criterion,
                    to_id=requirement,
                    relation=TraceRelation.REFINES,
                )
            )
            rules.append(
                CriterionRule(
                    rule_id=f"R-{criterion}",
                    criterion_id=criterion,
                    measure=measure,
                    comparator=Comparator.AT_LEAST,
                    threshold=Decimal("0.90"),
                    unit="ratio",
                    condition=condition,
                )
            )
            if positions:
                result = f"RES-{criterion}"
                items.append(
                    TraceItem(item_id=result, item_key=f"k-{result}", kind=TraceKind.RESULT)
                )
                results.append(
                    ResultRecord(
                        result_id=result,
                        criterion_id=criterion,
                        condition=condition,
                        value=Decimal("0.95"),
                        unit="ratio",
                        numerator=19,
                        denominator=20,
                        observed_at=T0,
                        source_span_refs=tuple(f"{criterion}#{n}" for n in range(positions)),
                    )
                )
    return TraceSet(
        items=tuple(items), links=tuple(links), rules=tuple(rules), results=tuple(results)
    )


async def opened_with(
    tmp_path: Path, trace_set: TraceSet
) -> tuple[Any, Rpc, ControlledResearchModel]:
    from tests.integration.test_research_request_v2 import setup

    model = ControlledResearchModel()
    tmp_path.mkdir(exist_ok=True)
    runtime = await setup(tmp_path, model)
    rpc = Rpc(runtime)
    await rpc.load(export_csv(trace_set), "CREATE")
    return runtime, rpc, model


async def investigate(
    rpc: Rpc, runtime: Any, model: ControlledResearchModel, row: str
) -> dict[str, Any]:
    origin = await origin_for(rpc, row)
    await rpc(
        "thread/start",
        project_id="p",
        problem="원인을 찾아 주세요.",
        contract_version=2,
        origin=origin,
    )
    await runtime.bus.drain()
    return with_origin(model)[0]


@pytest.mark.asyncio
async def test_the_row_carries_the_other_conditions_of_its_measure_with_their_values(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel()
    runtime, rpc = await opened(tmp_path, model)
    try:
        seen = await investigate(rpc, runtime, model, RAIN)
        siblings = {item["criterion_id"]: item for item in seen["sibling_verdicts"]}
        assert list(siblings) == [
            DRY,
            FOG,
        ]  # the one with a result first; the false-track rows are another measure
        dry = siblings[DRY]
        assert (dry["state"], dry["condition"], dry["value"]) == (
            "PASS_COMPUTED",
            "weather=dry",
            "0.95",
        )
        assert (dry["numerator"], dry["denominator"], dry["unit"]) == (19, 20, "ratio")
        assert dry["result_id"] == "SYN-RES-SYN-C-DET-DRY" and ">=" in dry["rule_summary"]
        fog = siblings[FOG]
        assert (
            fog["state"] == "HOLD_NO_RESULT" and fog["value"] is None and fog["result_id"] is None
        )
        assert seen["comparison_span_refs"] == ["30_RESULT_DRY_SYNTHETIC.yaml#detection"]
        assert "sibling" in seen["note"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_row_with_no_other_condition_of_its_measure_gets_nothing_added(
    tmp_path: Path,
) -> None:
    trace_set = criterion_set(
        {
            "REQ-1": [
                ("C-A", "detection_rate", "weather=dry", 1),
                ("C-B", "false_track_rate", "weather=dry", 1),
            ]
        }
    )
    runtime, rpc, model = await opened_with(tmp_path, trace_set)
    try:
        seen = await investigate(rpc, runtime, model, "C-A")
        assert seen["sibling_verdicts"] == [] and seen["comparison_span_refs"] == []
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_criterion_of_another_requirement_is_not_a_sibling(tmp_path: Path) -> None:
    trace_set = criterion_set(
        {
            "REQ-1": [
                ("C-A", "detection_rate", "weather=dry", 1),
                ("C-B", "detection_rate", "weather=rain", 1),
            ],
            "REQ-2": [("C-C", "detection_rate", "weather=fog", 1)],
        }
    )
    runtime, rpc, model = await opened_with(tmp_path, trace_set)
    try:
        seen = await investigate(rpc, runtime, model, "C-A")
        assert [item["criterion_id"] for item in seen["sibling_verdicts"]] == ["C-B"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_siblings_and_their_positions_are_capped_and_those_with_results_come_first(
    tmp_path: Path,
) -> None:
    others = [(f"C-{n}", "detection_rate", f"weather=w{n}", 0 if n < 2 else 5) for n in range(1, 8)]
    trace_set = criterion_set({"REQ-1": [("C-0", "detection_rate", "weather=w0", 1), *others]})
    runtime, rpc, model = await opened_with(tmp_path, trace_set)
    try:
        seen = await investigate(rpc, runtime, model, "C-0")
        ids = [item["criterion_id"] for item in seen["sibling_verdicts"]]
        assert len(ids) == MAX_SIBLING_VERDICTS == 4
        assert ids == ["C-2", "C-3", "C-4", "C-5"]  # C-1 has no result; later ones fill the room
        refs = seen["comparison_span_refs"]
        assert len(refs) == MAX_COMPARISON_REFS
        assert refs[:MAX_SIBLING_SPAN_REFS] == ["C-2#0", "C-2#1", "C-2#2"]  # each sibling is capped
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_requirement_row_has_no_siblings_and_another_project_is_never_mixed_in(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel()
    runtime, rpc = await opened(tmp_path, model)
    try:
        await rpc("project/create", project_id="q", name="other", cutoff_at="2026-09-13T00:00:00Z")
        other = criterion_set(
            {
                "SYN-REQ-RAD-001": [
                    (DRY, "detection_rate", "weather=dry", 1),
                    (RAIN, "detection_rate", "weather=rain", 1),
                ]
            }
        )
        text = export_csv(other)
        preview = await rpc("trace/importPreview", project_id="q", mode="CREATE", csv_text=text)
        await rpc(
            "trace/importApply",
            project_id="q",
            mode="CREATE",
            csv_text=text,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )
        seen = await investigate(rpc, runtime, model, RAIN)
        assert {item["criterion_id"] for item in seen["sibling_verdicts"]} == {DRY, FOG}
        # the other project's own result is called RES-SYN-C-DET-DRY; this project's is not that
        assert {item["result_id"] for item in seen["sibling_verdicts"]} == {
            "SYN-RES-SYN-C-DET-DRY",
            None,
        }
        requirement = await origin_for(rpc, "SYN-REQ-RAD-001", kind="REQUIREMENT")
        await rpc(
            "thread/start",
            project_id="p",
            problem="요구사항",
            contract_version=2,
            origin=requirement,
        )
        await runtime.bus.drain()
        row = with_origin(model)[-1]
        assert row["subject_kind"] == "REQUIREMENT"
        assert row["sibling_verdicts"] == [] and row["comparison_span_refs"] == []
    finally:
        runtime.close()
