"""The evidence a trace-row investigation gets from the connected demo files.

A real runtime with a controlled model (no live model): the demo files are connected the way the
file panel does, the trace is imported with sentence ids from the connected files, and a request
that starts from a trace row is run. What each model stage was given is read from the calls.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.integration.test_trace_origin import Rpc

from thoth.application.services.research_retrieval_policy import retrieval_policy
from thoth.application.services.trace_csv import export_csv
from thoth.apps.runtime import AppRuntime
from thoth.domain.canonical import model_digest
from thoth.domain.enums import ModelRole
from thoth.domain.evidence_requirements import EvidenceRanking
from thoth.domain.model import ModelRequest, ModelResult

REPO = Path(__file__).parents[2]
DEMO = REPO / "examples" / "synthetic-radar-demo-v1"
RAIN = "SYN-C-DET-RAIN"


def question_for(row: str) -> str:
    """The question the demo preparation helper asks for a trace row."""
    return f'「{row}」 기준의 판정이 "미달"입니다. 원인 후보와 그것을 가를 시험을 찾아 주세요.'


def _load_prepare() -> Any:
    spec = importlib.util.spec_from_file_location(
        "prepare_synthetic_radar_demo", REPO / "scripts" / "prepare_synthetic_radar_demo.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prepare = _load_prepare()


class RerankerPicksNothing(ControlledResearchModel):
    """The relevance stage returns no ids: only what the server keeps on its own survives."""

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        if request.role != ModelRole.EVIDENCE_RERANKER:
            return await super().structured(request)
        self.calls.append(request)  # type: ignore[arg-type]
        parsed = request.output_model.model_validate(
            EvidenceRanking(ordered_span_ids=(), rationale="none").model_dump()
        )
        return ModelResult(
            output=parsed,
            model_id="CONTROLLED_RESEARCH",
            scripted=True,
            prompt_version=request.prompt_version,
            input_digest=model_digest("INPUT", request.context_pack, schema_version="1.0.0"),
            output_digest=model_digest("OUTPUT", parsed, schema_version="1.0.0"),
        )


async def connect(rpc: Rpc, runtime: AppRuntime, tmp_path: Path, path: Path) -> str:
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    shutil.copy(path, inbox / path.name)
    media = "text/csv" if path.suffix == ".csv" else "text/plain"
    connected = await rpc(
        "project/source/connect",
        project_id="p",
        relative_path=path.name,
        media_type=media,
        authority="UNCLASSIFIED",
        cutoff_state="ELIGIBLE",
        security_class="INTERNAL",
    )
    return str(connected["artifact"]["artifact_id"])


async def sentence_ids(rpc: Rpc, artifact_id: str) -> dict[int, str]:
    listed = await rpc("evidence/list", project_id="p")
    return {
        int(span["locator"]["line"]): str(span["span_id"])
        for span in listed["evidence"]
        if span["artifact_id"] == artifact_id and span["locator"].get("line") is not None
    }


async def opened_demo(
    tmp_path: Path, model: ControlledResearchModel, *, phase: int = 2, with_csv: bool = False
) -> tuple[AppRuntime, Rpc]:
    """A project with the demo files connected and the demo trace imported (phase 1 or 2)."""
    runtime = await setup(tmp_path, model, source=False)
    rpc = Rpc(runtime)
    await connect(rpc, runtime, tmp_path, DEMO / prepare.REQUIREMENT_FILE)
    await connect(rpc, runtime, tmp_path, DEMO / prepare.TEST_PLAN_FILE)
    spans: dict[str, dict[str, str]] = {}
    for path in prepare.result_files(demo=DEMO):
        if prepare._yaml(path)["available_from_phase"] > phase:  # pyright: ignore[reportPrivateUsage]
            continue
        artifact = await connect(rpc, runtime, tmp_path, path)
        found = await sentence_ids(rpc, artifact)
        lines = prepare.value_lines(path.read_text(encoding="utf-8"))
        spans[path.name] = {part: found[lines[part]] for part in prepare.RESULT_PARTS}
        if with_csv:
            reference = prepare._yaml(path)["trials_csv"]  # pyright: ignore[reportPrivateUsage]
            await connect(rpc, runtime, tmp_path, DEMO / reference)
    await rpc.load(export_csv(prepare.build_trace_set(DEMO, phase, spans)), "CREATE")
    return runtime, rpc


async def start_from_row(rpc: Rpc, runtime: AppRuntime, subject_id: str) -> None:
    view = await rpc("trace/read", project_id="p")
    verdict = next(item for item in view["verdicts"] if item["subject_id"] == subject_id)
    origin = {
        "kind": "TRACE_VERDICT",
        "project_id": "p",
        "subject_kind": "CRITERION",
        "subject_id": subject_id,
        "verdict_revision": verdict["revision_digest"],
    }
    await rpc(
        "thread/start",
        project_id="p",
        problem=question_for(subject_id),
        contract_version=2,
        origin=origin,
    )
    await runtime.bus.drain()


def given(model: ControlledResearchModel, role: ModelRole) -> tuple[Any, ...]:
    """The evidence the first call of a role was given."""
    call = next(c for c in model.calls if c.role == role)
    return tuple(call.context_pack.evidence)


def texts(spans: tuple[Any, ...]) -> set[str]:
    return {span.exact_text for span in spans}


@pytest.mark.asyncio
async def test_a_pinned_span_survives_both_selection_stages_when_the_ranker_picks_nothing(
    tmp_path: Path,
) -> None:
    model = RerankerPicksNothing()
    runtime, rpc = await opened_demo(tmp_path, model)
    try:
        view = await rpc("trace/read", project_id="p")
        pinned = next(r for r in view["results"] if r["criterion_id"] == RAIN)["source_span_refs"]
        await start_from_row(rpc, runtime, RAIN)
        planner = given(model, ModelRole.RESEARCH_PLANNER)
        ranker = given(model, ModelRole.EVIDENCE_RERANKER)
        reviewer = given(model, ModelRole.SEMANTIC_REVIEWER)
        for name, stage in (("planner", planner), ("ranker", ranker), ("reviewer", reviewer)):
            assert set(pinned) <= {span.span_id for span in stage}, name
        assert 'value: "0.80"' in texts(reviewer)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_dry_result_and_its_summary_reach_the_model_for_a_rain_row(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel()
    runtime, rpc = await opened_demo(tmp_path, model, with_csv=True)
    try:
        await start_from_row(rpc, runtime, RAIN)
        final = texts(given(model, ModelRole.SEMANTIC_REVIEWER))
        # the other condition's counts are in what the reviewer reads, beside the row's own
        assert {"numerator: 19", "denominator: 20", 'value: "0.95"'} <= final
        assert {"numerator: 16", "denominator: 20", 'value: "0.80"'} <= final
        origin = next(
            c.context_pack.research_context["origin"]
            for c in model.calls
            if "origin" in c.context_pack.research_context
        )
        dry = next(s for s in origin["sibling_verdicts"] if s["criterion_id"] == "SYN-C-DET-DRY")
        assert (dry["state"], dry["value"], dry["numerator"], dry["denominator"]) == (
            "PASS_COMPUTED",
            "0.95",
            19,
            20,
        )
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_trial_files_are_connected_by_row_and_the_final_evidence_stays_in_budget(
    tmp_path: Path,
) -> None:
    model = ControlledResearchModel()
    runtime, rpc = await opened_demo(tmp_path, model, with_csv=True)
    try:
        listed = (await rpc("evidence/list", project_id="p"))["evidence"]
        rows = [s for s in listed if s["extraction_method"].startswith("csv:")]
        assert {s["extraction_method"] for s in rows} == {"csv:1.1.0"}
        pointers = [s["locator"]["json_pointer"] for s in rows]
        assert pointers.count("/header") == 2  # one header node for each trial file
        assert all(p == "/header" or p.startswith("/rows/") for p in pointers)
        assert any("row_id=SYN-T-RAIN-03" in s["exact_text"] for s in rows)
        await start_from_row(rpc, runtime, RAIN)
        final = given(model, ModelRole.SEMANTIC_REVIEWER)
        policy = retrieval_policy()
        assert len(final) <= policy.max_spans
        assert sum(len(s.exact_text) for s in final) <= policy.character_budget
    finally:
        runtime.close()
