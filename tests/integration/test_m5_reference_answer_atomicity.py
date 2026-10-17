"""Reference answer and grouped-answer atomicity (97-V, test only).

begin() already has storage-fault coverage (test_reference_storage_boundary). This file adds the
same evidence for answer(): each target writer runs the original first and then raises
(fail_after), the whole domain snapshot is compared at the boundary where the research result has
already been committed (the reference phase is its own transaction,
REFERENCE-PHASE-AFTER-RESEARCH-SEPARATE-COMMIT), and a reopened runtime shows the same state.
Grouped natural-language answers run several answer() calls inside one outer
ReferenceService.transaction in ReferenceThreadEntry; a fault at the second answer must leave no
revision of the first one. A same-key replay and a new-key retry follow. The mapper is the scripted
one used by test_reference_model_mapping; no real model is called. This file observes the current
contract and fixes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel
from tests.integration.reference_helpers import ReferenceHarness, reference_harness
from tests.integration.storage_coverage_helpers import domain_snapshot, fail_after, value
from tests.integration.test_a02_autonomous_acquisition import DynamicA02Model

from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.application.services.reference_mapping import ModelReferenceMapper
from thoth.application.services.reference_service import ReferenceService, ReferenceTransition
from thoth.application.services.revision_service import RevisionCommitService
from thoth.apps.runtime import create_runtime
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.enums import ModelRole
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.reference import ReferenceAnswerRequest

T = TypeVar("T", bound=BaseModel)
OUT = Path(__file__).resolve().parents[2] / "outputs/refactor-step97-20261015"
OWNERS: dict[str, tuple[type[object], str]] = {
    "projection": (SqliteCriterionContractStore, "add_contract"),
    "audit": (SqliteCriterionContractStore, "append_audit"),
    "receipt": (RevisionCommitService, "commit"),
    "thread": (SqliteThreadStore, "update"),
}
FAULTS = list(OWNERS)
GROUPED_INSTRUCTION = (
    "Use per-request as the denominator and declared test population as the population."
)
GROUPED_ANSWERS = [
    {"field": "denominator", "value": "per-request", "quote": "per-request"},
    {
        "field": "population",
        "value": "declared test population",
        "quote": "declared test population",
    },
]


def save(name: str, data: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"v97-{name}.json").write_text(
        json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8"
    )


def without_envelope(snapshot: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """The behavior envelope (control_records) records the failed request itself."""

    return {name: rows for name, rows in snapshot.items() if name != "control_records"}


def changed_tables(
    before: dict[str, tuple[str, ...]], after: dict[str, tuple[str, ...]]
) -> dict[str, int]:
    return {
        name: len(set(after[name]) ^ set(before[name]))
        for name in after
        if after[name] != before.get(name)
    }


async def interview(h: ReferenceHarness, *missing: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """A pending inquiry that asks for the given target fields; (inquiry, current criterion)."""

    target = {**h.request["target"], **{field: None for field in missing}}
    first = value(await h.input("ask", reference_request={**h.request, "target": target}))
    inquiry = first["reference_inquiry"]
    assert inquiry["state"] == "NEEDS_INPUT"
    return inquiry, await h.read("read-after-ask")


def answer_body(
    h: ReferenceHarness, inquiry: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    question = next(q for q in inquiry["questions"] if q["field"] == "denominator")
    return {
        "criterion_id": h.criterion["criterion_id"],
        "expected_revision_digest": current["revision_digest"],
        "inquiry_id": inquiry["inquiry_id"],
        "question_id": question["question_id"],
        "value": "per-request",
    }


def script_mapper(monkeypatch: pytest.MonkeyPatch, h: ReferenceHarness) -> list[str]:
    """Scripted reference mapper: measurements first, then two grounded answers."""

    original = DynamicA02Model.structured
    calls: list[str] = []

    async def mapped(instance: DynamicA02Model, request: ModelRequest[T]) -> ModelResult[T]:
        if request.role != ModelRole.REFERENCE_MAPPER:
            return await original(instance, request)
        calls.append(request.context_pack.problem)
        proposal: dict[str, Any] = (
            {"answers": GROUPED_ANSWERS}
            if "Use per-request" in request.context_pack.problem
            else {
                "measurements": h.request["measurements"],
                "missing_field_order": ["denominator", "population"],
            }
        )
        output = request.output_model.model_validate(proposal)
        return ModelResult(
            output=output,
            model_id="SCRIPTED_REFERENCE_MAPPER",
            prompt_version=request.prompt_version,
            scripted=True,
            input_digest=domain_digest(
                "MODEL_INPUT", "1.0.0", canonical_payload(request.context_pack)
            ),
            output_digest=domain_digest("MODEL_OUTPUT", "1.0.0", canonical_payload(output)),
        )

    monkeypatch.setattr(DynamicA02Model, "structured", mapped)
    return calls


async def grouped_interview(h: ReferenceHarness) -> tuple[dict[str, Any], dict[str, Any]]:
    first = value(
        await h.input(
            "map",
            instruction="Map the selected observations and identify missing target conditions.",
            reference_request={
                **h.request,
                "measurements": None,
                "target": {**h.request["target"], "denominator": None, "population": None},
            },
        )
    )
    inquiry = first["reference_inquiry"]
    assert inquiry["state"] == "NEEDS_INPUT"
    assert {q["field"] for q in inquiry["questions"]} >= {"denominator", "population"}
    return inquiry, await h.read("read-after-map")


def capture_before_grouped_writes(
    monkeypatch: pytest.MonkeyPatch, h: ReferenceHarness
) -> dict[str, Any]:
    """Snapshot right after the mapper returned: research is committed, no answer is written."""

    original = ModelReferenceMapper.map
    seen: dict[str, Any] = {}

    async def spy(instance: ModelReferenceMapper, **kwargs: Any) -> Any:
        result = await original(instance, **kwargs)
        if result.proposal.answers:
            seen["before"] = domain_snapshot(h.runtime.ledger.engine)
        return result

    monkeypatch.setattr(ModelReferenceMapper, "map", spy)
    return seen


def reopened(tmp_path: Path, h: ReferenceHarness) -> None:
    runtime = create_runtime(tmp_path / "allowed")
    try:
        assert domain_snapshot(runtime.ledger.engine) == domain_snapshot(h.runtime.ledger.engine)
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", FAULTS)
async def test_reference_answer_rolls_back_every_domain_table_after_storage_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    async with reference_harness(tmp_path) as h:
        inquiry, current = await interview(h, "denominator")
        body = answer_body(h, inquiry, current)
        original = ReferenceService.answer
        checked: list[dict[str, Any]] = []

        def faulty_answer(
            instance: ReferenceService,
            project: str,
            thread: str,
            request: ReferenceAnswerRequest,
            **kwargs: Any,
        ) -> ReferenceTransition:
            before = domain_snapshot(h.runtime.ledger.engine)
            with monkeypatch.context() as patch:
                owner, method = OWNERS[fault]
                hits = fail_after(patch, owner, method)
                with pytest.raises(RuntimeError, match="injected storage failure"):
                    original(instance, project, thread, request, **kwargs)
            assert hits == [OWNERS[fault][1]]
            assert domain_snapshot(h.runtime.ledger.engine) == before
            checked.append({"fault": fault, "hits": hits})
            raise RuntimeError("verified reference answer rollback")

        monkeypatch.setattr(ReferenceService, "answer", faulty_answer)
        result = await h.input("fault-answer", reference_answer=body)
        assert result.error is not None and len(checked) == 1
        assert (await h.read("read-after-fault"))["revision_digest"] == current["revision_digest"]
        reopened(tmp_path, h)
        save(f"answer-{fault}", {"checked": checked, "error": str(result.error)[:200]})


async def test_grouped_answers_commit_one_revision_per_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with reference_harness(tmp_path) as h:
        calls = script_mapper(monkeypatch, h)
        inquiry, current = await grouped_interview(h)
        seen = capture_before_grouped_writes(monkeypatch, h)
        answered = value(await h.input("natural-answers", instruction=GROUPED_INSTRUCTION))
        after = answered["reference_inquiry"]
        assert after["inquiry_id"] == inquiry["inquiry_id"]
        assert [a["value"] for a in after["answers"]] == [
            "per-request",
            "declared test population",
        ]
        assert after["state"] == "CALCULATED", after["reason_codes"]
        assert len(calls) == 2 and "before" in seen
        delta = changed_tables(seen["before"], domain_snapshot(h.runtime.ledger.engine))
        # Two criterion revisions, one per answer, and nothing else beside the audit trail.
        assert delta["criterion_contracts"] == 2
        assert (await h.read("read-after-grouped"))["revision_digest"] != current["revision_digest"]
        save("grouped-success", {"changed_tables": delta})


@pytest.mark.parametrize("fault", ["projection", "thread"])
async def test_second_grouped_answer_fault_leaves_no_revision_of_the_first_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    async with reference_harness(tmp_path) as h:
        script_mapper(monkeypatch, h)
        _inquiry, current = await grouped_interview(h)
        seen = capture_before_grouped_writes(monkeypatch, h)
        original = ReferenceService.answer
        order: list[str] = []

        def counting(
            instance: ReferenceService,
            project: str,
            thread: str,
            request: ReferenceAnswerRequest,
            **kwargs: Any,
        ) -> ReferenceTransition:
            order.append("answer")
            if len(order) != 2:
                return original(instance, project, thread, request, **kwargs)
            with monkeypatch.context() as patch:
                owner, method = OWNERS[fault]
                fail_after(patch, owner, method)
                return original(instance, project, thread, request, **kwargs)

        monkeypatch.setattr(ReferenceService, "answer", counting)
        failed = await h.input("natural-answers-fault", instruction=GROUPED_INSTRUCTION)
        assert failed.error is not None and order == ["answer", "answer"]
        after = domain_snapshot(h.runtime.ledger.engine)
        delta = changed_tables(without_envelope(seen["before"]), without_envelope(after))
        assert delta == {}, delta
        assert (await h.read("read-after-fault"))["revision_digest"] == current["revision_digest"]
        reopened(tmp_path, h)
        save(f"grouped-second-{fault}", {"calls": order, "changed_tables": delta})


async def test_answer_fault_then_same_key_replay_and_new_key_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with reference_harness(tmp_path) as h:
        inquiry, current = await interview(h, "denominator")
        body = answer_body(h, inquiry, current)
        before = domain_snapshot(h.runtime.ledger.engine)["criterion_contracts"]
        with monkeypatch.context() as patch:
            hits = fail_after(patch, SqliteCriterionContractStore, "add_contract")
            failed = await h.input("answer-fault", reference_answer=body)
        assert failed.error is not None and hits == ["add_contract"]
        assert domain_snapshot(h.runtime.ledger.engine)["criterion_contracts"] == before
        assert (await h.read("read-after-fault"))["revision_digest"] == current["revision_digest"]
        replay = await h.input("answer-fault", reference_answer=body)
        assert replay.error == failed.error
        assert domain_snapshot(h.runtime.ledger.engine)["criterion_contracts"] == before
        retried = value(await h.input("answer-retry", reference_answer=body))
        assert retried["reference_inquiry"]["answers"][-1]["revision"] == 1
        rows = domain_snapshot(h.runtime.ledger.engine)["criterion_contracts"]
        assert len(rows) == len(before) + 1
        save("answer-replay-retry", {"rows_before": len(before), "rows_after": len(rows)})


async def test_grouped_fault_then_same_key_replay_and_new_key_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with reference_harness(tmp_path) as h:
        script_mapper(monkeypatch, h)
        _inquiry, current = await grouped_interview(h)
        before = domain_snapshot(h.runtime.ledger.engine)["criterion_contracts"]
        original = SqliteCriterionContractStore.add_contract
        writes: list[str] = []

        def second_write_fails(store: SqliteCriterionContractStore, record: Any) -> None:
            original(store, record)
            writes.append(record.revision_digest)
            if len(writes) == 2:
                raise RuntimeError("injected storage failure after add_contract")

        with monkeypatch.context() as patch:
            patch.setattr(SqliteCriterionContractStore, "add_contract", second_write_fails)
            failed = await h.input("natural-fault", instruction=GROUPED_INSTRUCTION)
        assert failed.error is not None and len(writes) == 2
        assert domain_snapshot(h.runtime.ledger.engine)["criterion_contracts"] == before
        assert (await h.read("read-after-fault"))["revision_digest"] == current["revision_digest"]
        replay = await h.input("natural-fault", instruction=GROUPED_INSTRUCTION)
        assert replay.error == failed.error
        assert domain_snapshot(h.runtime.ledger.engine)["criterion_contracts"] == before
        retried = value(await h.input("natural-retry", instruction=GROUPED_INSTRUCTION))
        assert [a["revision"] for a in retried["reference_inquiry"]["answers"]] == [1, 1]
        rows = domain_snapshot(h.runtime.ledger.engine)["criterion_contracts"]
        assert len(rows) == len(before) + 2
        save("grouped-replay-retry", {"rows_before": len(before), "rows_after": len(rows)})
