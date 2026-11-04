"""Evidence, criterion, hypothesis, action and decision-object commands are one write each.

Every case runs the public command, fails right after the store's `point` write, and checks that no
table other than the failed command's own journal rows changed. The same request must then succeed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.fault_helpers import expect_rolled_back_and_retry
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import (
    create_hypothesis,
    create_link,
    creation,
    link_input,
    setup,
)
from tests.integration.test_a09_storage_atomicity import compile_contract, correction, prepare

from thoth.adapters.storage.acquisition_trace import SqliteEvidenceUnitOfWork
from thoth.adapters.storage.action import SqliteActionStore
from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.adapters.storage.decision_object import SqliteDecisionObjectStore
from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.adapters.storage.hypothesis import SqliteHypothesisStore


@pytest.mark.parametrize("point", ["add_contract", "append_audit"])
async def test_criteria_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime, project, spans = await prepare(tmp_path / "workspace")
    try:
        body: dict[str, object] = {"project_id": project, "source_span_ids": spans}
        retried = await expect_rolled_back_and_retry(
            runtime, monkeypatch, SqliteCriterionContractStore, point, "criteria/compile", body
        )
        assert retried["criterion"]["criterion_id"]
    finally:
        runtime.close()


@pytest.mark.parametrize("point", ["add_conflict", "add_contract", "append_audit"])
async def test_criteria_field_correct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime, project, spans = await prepare(tmp_path / "workspace")
    try:
        current = (await compile_contract(runtime, project, spans))["criterion"]
        body: dict[str, object] = correction(project, current, spans)
        await expect_rolled_back_and_retry(
            runtime,
            monkeypatch,
            SqliteCriterionContractStore,
            point,
            "criteria/field/correct",
            body,
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("point", ["add_reference", "append_audit"])
async def test_criteria_reference_generate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime, project, spans = await prepare(tmp_path / "workspace")
    try:
        current = (await compile_contract(runtime, project, spans))["criterion"]
        body: dict[str, object] = {
            "project_id": project,
            "criterion_id": current["criterion_id"],
            "expected_revision_digest": current["revision_digest"],
            "source_scope": spans,
            "scenarios": ["candidate-only"],
        }
        await expect_rolled_back_and_retry(
            runtime,
            monkeypatch,
            SqliteCriterionContractStore,
            point,
            "criteria/reference/generate",
            body,
        )
    finally:
        runtime.close()


EVIDENCE_CASES: list[tuple[str, type[object], str]] = [
    ("evidence/link/propose", SqliteEvidenceUnitOfWork, "commit"),
    ("evidence/source/metadata/correct", SqliteEvidenceGraphStore, "add_source"),
    ("evidence/source/metadata/correct", SqliteEvidenceGraphStore, "append_audit"),
    ("evidence/challenge", SqliteEvidenceGraphStore, "add_conflict"),
    ("evidence/revalidate", SqliteEvidenceGraphStore, "add_link"),
    ("evidence/span/correct", SqliteEvidenceGraphStore, "add_span_correction"),
]


@pytest.mark.parametrize("method,store,point", EVIDENCE_CASES)
async def test_evidence_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, store: type[object], point: str
) -> None:
    runtime, ctx = await setup(tmp_path / "workspace")
    try:
        project = ctx["project"]
        if method == "evidence/link/propose":
            body: dict[str, object] = link_input(ctx)
        else:
            link = await create_link(runtime, ctx)
            bodies: dict[str, dict[str, object]] = {
                "evidence/source/metadata/correct": {
                    "source_id": ctx["source"],
                    "rights": "INTERNAL",
                },
                "evidence/challenge": {
                    "evidence_ids": [link["evidence_id"]],
                    "field": "clock",
                    "reason": "drift",
                },
                "evidence/revalidate": {"evidence_id": link["evidence_id"]},
                "evidence/span/correct": {
                    "span_id": ctx["spans"][0],
                    "corrected_text": "Clock drift observed",
                    "reason": "spelling correction",
                },
            }
            body = {"project_id": project, **bodies[method]}
        await expect_rolled_back_and_retry(runtime, monkeypatch, store, point, method, body)
    finally:
        runtime.close()


@pytest.mark.parametrize("point", ["add_hypothesis", "append_audit"])
async def test_hypothesis_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime, ctx = await setup(tmp_path / "workspace")
    try:
        retried = await expect_rolled_back_and_retry(
            runtime, monkeypatch, SqliteHypothesisStore, point, "hypothesis/create", creation(ctx)
        )
        assert retried["hypothesis"]["statement"] == creation(ctx)["statement"]
    finally:
        runtime.close()


HYPOTHESIS_AUXILIARY = [
    ("hypothesis/assumption/add", "add_assumption"),
    ("hypothesis/appraise", "add_appraisal"),
]


@pytest.mark.parametrize("method,point", HYPOTHESIS_AUXILIARY)
async def test_hypothesis_auxiliary_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, point: str
) -> None:
    runtime, ctx = await setup(tmp_path / "workspace")
    try:
        current = await create_hypothesis(runtime, ctx)
        common: dict[str, object] = {
            "project_id": ctx["project"],
            "hypothesis_id": current["hypothesis_id"],
        }
        bodies: dict[str, dict[str, object]] = {
            "hypothesis/assumption/add": {
                **common,
                "expected_revision_digest": current["revision_digest"],
                "statement": "Measurement clock is calibrated",
                "role": "MEASUREMENT",
                "evidence_refs": ctx["spans"],
                "validation_route": "calibration record",
            },
            "hypothesis/appraise": {
                **common,
                "evidence_refs": ctx["spans"],
                "test_assessment_refs": [],
                "appraisal_scope": {"workstream": "integration"},
            },
        }
        await expect_rolled_back_and_retry(
            runtime, monkeypatch, SqliteHypothesisStore, point, method, bodies[method]
        )
    finally:
        runtime.close()


async def generated_actions(runtime: Any, ctx: dict[str, Any]) -> list[str]:
    body: dict[str, object] = {
        "project_id": ctx["project"],
        "object_id": ctx["object"],
        "decision_need": "Compare bounded alternatives",
        "evidence_scope": ctx["spans"],
    }
    generated = value(await runtime.bus.dispatch(request("action/generate", "actions", body)))
    return [row["action_id"] for row in generated["actions"]]


async def test_action_generate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, ctx = await setup(tmp_path / "workspace")
    try:
        body: dict[str, object] = {
            "project_id": ctx["project"],
            "object_id": ctx["object"],
            "decision_need": "Compare bounded alternatives",
            "evidence_scope": ctx["spans"],
        }
        retried = await expect_rolled_back_and_retry(
            runtime, monkeypatch, SqliteActionStore, "add_action", "action/generate", body, nth=2
        )
        assert retried["actions"]
    finally:
        runtime.close()


async def test_action_portfolio_compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, ctx = await setup(tmp_path / "workspace")
    try:
        ids = await generated_actions(runtime, ctx)
        body: dict[str, object] = {
            "project_id": ctx["project"],
            "object_id": ctx["object"],
            "action_ids": ids,
            "decision_need": "Choose an alternative",
        }
        retried = await expect_rolled_back_and_retry(
            runtime,
            monkeypatch,
            SqliteActionStore,
            "add_portfolio",
            "action/portfolio/compose",
            body,
        )
        assert retried["portfolio"]["portfolio_id"]
    finally:
        runtime.close()


@pytest.mark.parametrize("point", ["append_audit"])
async def test_object_frame_revise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime, ctx = await setup(tmp_path / "workspace")
    try:
        read: dict[str, object] = {"project_id": ctx["project"], "object_id": ctx["object"]}
        current = value(await runtime.bus.dispatch(request("object/read", "object", read)))[
            "object"
        ]
        body: dict[str, object] = {
            "project_id": ctx["project"],
            "object_id": ctx["object"],
            "expected_revision_digest": current["revision_digest"],
            "frame_patch": {"purpose_statement": "A current scoped frame"},
            "evidence_refs": ctx["spans"],
            "reason": "update",
        }
        await expect_rolled_back_and_retry(
            runtime, monkeypatch, SqliteDecisionObjectStore, point, "object/frame/revise", body
        )
    finally:
        runtime.close()
