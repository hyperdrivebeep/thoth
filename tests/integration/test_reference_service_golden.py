"""ReferenceService golden (97): what the service returns and which ports it calls.

2026-10-26: text parser 1.2 metadata and dependent digests were refreshed after an exact
1.1 parser control and unchanged call/result shapes. Equality checks remain exact.

Scenarios run on a real runtime through thread/input (the only public entry) with the scripted
reference mapper, the local deterministic calculators, a deterministic id generator and clock. The
trace holds the calls made while a ReferenceService method is running: method entries and exits with
argument and result digests, criterion store, artifact, thread store, ledger, revision commit,
calculator and mapper calls, the id and clock sequence and every transaction entry. The golden file
is generated from the pre-move service (THOTH_WRITE_GOLDEN=1), reviewed and frozen: moving the pure
record construction out of the service must leave results, call order, counts and digests unchanged.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel
from tests.integration.reference_helpers import TARGET, ReferenceHarness, reference_harness
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_m5_reference_answer_atomicity import (
    GROUPED_INSTRUCTION,
    answer_body,
    grouped_interview,
    interview,
    script_mapper,
)

from thoth.adapters.reference_calculators.registry import (
    ApprovedFormulaCalculator,
    ObservedRangeCalculator,
    ReferenceCalculatorRegistry,
)
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.adapters.storage.sqlite import SqliteLedger
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.application.services.criterion_contract_service import CriterionContractService
from thoth.application.services.reference_mapping import ModelReferenceMapper
from thoth.application.services.reference_service import ReferenceService
from thoth.application.services.revision_service import RevisionCommitService
from thoth.domain.reference import ReferenceError

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "reference_service_golden.json"
BASE = datetime(2026, 10, 15, tzinfo=UTC)
SCENARIOS = [
    "begin_range",
    "begin_needs_input",
    "begin_without_measurements",
    "begin_scenarios",
    "begin_formula",
    "begin_stale_revision",
    "begin_unknown_criterion",
    "answer_single",
    "answer_revision_conflict",
    "mapping_begin",
    "grouped_natural_answers",
    "automatic_requests",
]
SERVICE_METHODS = (
    "transaction",
    "current",
    "sources",
    "authorize",
    "begin",
    "answer",
    "_evaluate",
    "_scenarios",
    "_grounded",
    "_persist",
    "mapping_sources",
    "source_digests",
    "automatic_request",
)
PORTS: list[tuple[Any, str, tuple[str, ...]]] = [
    (
        SqliteCriterionContractStore,
        "C",
        ("read_contract", "list_contracts", "add_contract", "append_audit"),
    ),
    (SqliteArtifactLedger, "A", ("read_evidence", "read_artifact")),
    (SqliteThreadStore, "T", ("read", "update")),
    (SqliteLedger, "L", ("read_heads", "transaction")),
    (RevisionCommitService, "R", ("commit",)),
    (CriterionContractService, "S", ("transaction", "revise")),
    (ReferenceCalculatorRegistry, "K", ("resolve",)),
    (ObservedRangeCalculator, "O", ("calculate",)),
    (ApprovedFormulaCalculator, "F", ("calculate",)),
    (ModelReferenceMapper, "M", ("map",)),
]


def plain(item: Any) -> Any:
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json")
    if isinstance(item, datetime):
        return item.isoformat()
    if isinstance(item, dict):
        return {str(k): plain(v) for k, v in cast(dict[Any, Any], item).items()}
    if isinstance(item, (list, tuple, set, frozenset)):
        return [plain(v) for v in cast(Iterable[Any], item)]
    return item


def summ(item: Any) -> Any:
    if item is None or isinstance(item, (str, int, float, bool)):
        return item
    if isinstance(item, datetime):
        return item.isoformat()
    kind = type(item).__name__
    if not isinstance(item, (BaseModel, dict, list, tuple, set, frozenset)):
        return kind
    text = json.dumps(plain(cast(Any, item)), sort_keys=True, default=str)
    return f"{kind}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


class Recorder:
    def __init__(self) -> None:
        self.on = False
        self.depth = 0
        self.log: list[list[Any]] = []
        self.results: list[list[Any]] = []
        self.ids = 0
        self.ticks = 0
        self.services: list[ReferenceService] = []

    def note(self, *entry: Any) -> None:
        if self.on and self.depth > 0:
            self.log.append(list(entry))

    def result(self, name: str, item: Any) -> None:
        self.results.append([name, plain(item)])


def arguments(args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[Any]:
    return [*[summ(a) for a in args], *[f"{k}={summ(v)}" for k, v in sorted(kwargs.items())]]


def install(monkeypatch: pytest.MonkeyPatch, rec: Recorder) -> None:
    def new(self: Any, prefix: str) -> str:
        normalized = prefix.strip().lower().replace("_", "-")
        rec.ids += 1
        rec.note("ids", normalized)
        return f"{normalized}:{rec.ids:08d}"

    def now(self: Any) -> datetime:
        rec.ticks += 1
        rec.note("clock")
        return BASE + timedelta(milliseconds=rec.ticks)

    monkeypatch.setattr(UuidIdGenerator, "new", new)
    monkeypatch.setattr(SystemClock, "now", now)

    init = ReferenceService.__init__

    def capture(self: ReferenceService, *args: Any, **kwargs: Any) -> None:
        init(self, *args, **kwargs)
        rec.services.append(self)

    monkeypatch.setattr(ReferenceService, "__init__", capture)

    def wrap_service(name: str) -> None:
        original = getattr(ReferenceService, name)

        def traced(self: Any, *args: Any, **kwargs: Any) -> Any:
            rec.depth += 1
            rec.note("S", "enter", name, *arguments(args, kwargs))
            try:
                result = original(self, *args, **kwargs)
            except BaseException as error:
                rec.note("S", "raise", name, type(error).__name__, str(error))
                rec.depth -= 1
                raise
            rec.note("S", "exit", name, summ(result))
            rec.depth -= 1
            return result

        monkeypatch.setattr(ReferenceService, name, traced)

    def wrap_port(owner: Any, short: str, name: str) -> None:
        original = getattr(owner, name)
        if inspect.iscoroutinefunction(original):

            async def traced_async(self: Any, *args: Any, **kwargs: Any) -> Any:
                rec.note(short, name, *arguments(args, kwargs))
                return await original(self, *args, **kwargs)

            monkeypatch.setattr(owner, name, traced_async)
            return

        def traced(self: Any, *args: Any, **kwargs: Any) -> Any:
            rec.note(short, name, *arguments(args, kwargs))
            return original(self, *args, **kwargs)

        monkeypatch.setattr(owner, name, traced)

    for method in SERVICE_METHODS:
        wrap_service(method)
    for owner, short, names in PORTS:
        for method in names:
            wrap_port(owner, short, method)


def reference_part(response: Any) -> dict[str, Any]:
    if response.error is not None:
        error = response.error
        return {"error": {"code": error.code, "message": error.message, "data": plain(error.data)}}
    result = value(response)
    return {
        key: result.get(key)
        for key in ("reference_inquiry", "reference_commit", "reference_criterion_revision")
    }


async def traced_input(rec: Recorder, h: ReferenceHarness, key: str, **extra: Any) -> None:
    rec.on = True
    try:
        response = await h.input(key, **extra)
    finally:
        rec.on = False
    rec.result("reference", reference_part(response))


def formula_binding() -> str:
    return "b" * 64


async def formula_setup(h: ReferenceHarness, target: dict[str, str]) -> dict[str, Any]:
    corrected = value(
        await h.runtime.bus.dispatch(
            request(
                "criteria/field/correct",
                "bind-formula",
                {
                    "project_id": h.project,
                    "criterion_id": h.criterion["criterion_id"],
                    "expected_revision_digest": h.criterion["revision_digest"],
                    "field_path": "computation_spec",
                    "proposed_value": {
                        "type": "FORMULA",
                        "expression": target["formula"],
                        "unit": "%",
                        "input_units": {"n": "count", "d": "count"},
                        "evaluator_binding_digest": formula_binding(),
                    },
                    "evidence_span_ids": h.request["source_refs"],
                    "reason": "Bind the declared synthetic source formula",
                },
            )
        )
    )["criterion"]
    return value(
        await h.runtime.bus.dispatch(
            request(
                "criteria/revalidate",
                "ready-formula",
                {
                    "project_id": h.project,
                    "criterion_id": h.criterion["criterion_id"],
                    "revision_digest": corrected["revision_digest"],
                    "trigger_reason": "source fields bound",
                },
            )
        )
    )["criterion"]


FORMULA_TARGET = {
    **TARGET,
    "metric_definition": "sample ratio",
    "formula": "n / d * 100",
    "unit": "%",
    "denominator": "cohort count",
    "measurement_method": "count observation",
}


async def automatic(rec: Recorder, h: ReferenceHarness) -> None:
    service = rec.services[-1]
    threads = SqliteThreadStore(h.runtime.ledger.engine)

    def set_scope(**scope: str) -> None:
        thread = threads.read(h.thread)
        assert thread is not None
        base = {k: v for k, v in thread.scope.items() if not k.startswith("reference_")}
        updated = thread.model_copy(
            update={"scope": {**base, **scope}, "revision": thread.revision + 1}
        )
        assert threads.update(updated, expected_revision=thread.revision)

    def call(label: str) -> None:
        rec.on = True
        try:
            try:
                outcome: Any = service.automatic_request(h.project, h.thread)
            except ReferenceError as exc:
                outcome = {"error": exc.code}
        finally:
            rec.on = False
        rec.result(label, outcome)

    criterion = h.criterion["criterion_id"]
    calc = "observed-range@1.0.0"
    call("nothing_pending")
    set_scope(reference_criterion_id=criterion)
    call("calculator_selection_required")
    set_scope(reference_criterion_id=criterion, reference_calculator="observed-range")
    call("calculator_format_invalid")
    set_scope(
        reference_criterion_id=criterion, reference_calculator=calc, reference_lane="NOT_A_LANE"
    )
    call("lane_invalid")
    set_scope(reference_criterion_id=criterion, reference_calculator=calc)
    call("fresh_default_lane")
    set_scope(
        reference_criterion_id=criterion,
        reference_calculator="approved-formula@1.0.0",
        reference_lane="RECALCULATED_VALUE",
    )
    call("fresh_formula_lane")
    set_scope(reference_criterion_id="criterion:missing", reference_calculator=calc)
    call("criterion_not_found")
    set_scope()
    await interview(h, "denominator")
    call("pending_inquiry")
    set_scope(reference_criterion_id=criterion)
    call("scoped_with_prior_inquiry")


async def scenario(name: str, path: Path, monkeypatch: pytest.MonkeyPatch, rec: Recorder) -> None:
    kwargs: dict[str, Any] = {}
    if name == "begin_scenarios":
        kwargs["observations"] = (
            ("first.md", "120", "ms", None),
            ("second.md", "0.14", "s", None),
            ("third.md", "160", "ms", None),
        )
    if name == "begin_formula":
        kwargs["target"] = FORMULA_TARGET
        kwargs["observations"] = (
            ("numerator.md", "12", "count", "n"),
            ("denominator.md", "200", "count", "d"),
        )
    async with reference_harness(path, **kwargs) as h:
        body = h.request
        if name == "begin_range":
            await traced_input(rec, h, "g-begin", reference_request=body)
        elif name == "begin_needs_input":
            target = {**body["target"], "denominator": None}
            await traced_input(rec, h, "g-needs", reference_request={**body, "target": target})
        elif name == "begin_without_measurements":
            await traced_input(rec, h, "g-none", reference_request={**body, "measurements": []})
        elif name == "begin_scenarios":
            refs = [m["span_id"] for m in body["measurements"]]
            scenarios = [
                {
                    "scenario_id": "first-two",
                    "label": "First two",
                    "measurement_span_ids": refs[:2],
                },
                {"scenario_id": "last-two", "label": "Last two", "measurement_span_ids": refs[1:]},
                {"scenario_id": "single", "label": "Single", "measurement_span_ids": refs[:1]},
                {
                    "scenario_id": "unbound",
                    "label": "Unbound",
                    "measurement_span_ids": ["span:none"],
                },
            ]
            await traced_input(
                rec, h, "g-scenarios", reference_request={**body, "scenarios": scenarios}
            )
        elif name == "begin_formula":
            approved = await formula_setup(h, FORMULA_TARGET)
            await traced_input(
                rec,
                h,
                "g-formula",
                reference_request={
                    **body,
                    "expected_revision_digest": approved["revision_digest"],
                    "lane": "RECALCULATED_VALUE",
                    "calculator_id": "approved-formula",
                    "evaluator_binding_digest": formula_binding(),
                },
            )
        elif name == "begin_stale_revision":
            stale = {**body, "expected_revision_digest": "0" * 64}
            await traced_input(rec, h, "g-stale", reference_request=stale)
        elif name == "begin_unknown_criterion":
            await traced_input(
                rec, h, "g-unknown", reference_request={**body, "criterion_id": "criterion:missing"}
            )
        elif name in {"answer_single", "answer_revision_conflict"}:
            inquiry, current = await interview(h, "denominator")
            answer = answer_body(h, inquiry, current)
            if name == "answer_revision_conflict":
                value(await h.input("g-first-answer", reference_answer=answer))
                current = await h.read("read-after-first")
                answer = {**answer, "expected_revision_digest": current["revision_digest"]}
            await traced_input(rec, h, "g-answer", reference_answer=answer)
        elif name == "mapping_begin":
            script_mapper(monkeypatch, h)
            target = {**body["target"], "denominator": None, "population": None}
            await traced_input(
                rec,
                h,
                "g-map",
                instruction="Map the selected observations and identify missing target conditions.",
                reference_request={**body, "measurements": None, "target": target},
            )
        elif name == "grouped_natural_answers":
            script_mapper(monkeypatch, h)
            await grouped_interview(h)
            await traced_input(rec, h, "g-natural", instruction=GROUPED_INSTRUCTION)
        elif name == "automatic_requests":
            await automatic(rec, h)
        else:
            raise AssertionError(name)


def load_golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}


@pytest.mark.parametrize("name", SCENARIOS)
async def test_reference_service_results_and_call_order_are_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    rec = Recorder()
    install(monkeypatch, rec)
    await scenario(name, tmp_path / "workspace", monkeypatch, rec)
    observed: dict[str, Any] = {"trace": rec.log, "results": rec.results}
    assert observed["trace"], "the traced step made no service call"
    if os.environ.get("THOTH_WRITE_GOLDEN") == "1":
        target = Path(os.environ.get("THOTH_GOLDEN_OUT", str(GOLDEN)))
        data: dict[str, Any] = (
            json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        )
        data[name] = observed
        target.write_text(
            json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        return
    expected = load_golden()[name]
    assert observed["trace"] == expected["trace"]
    assert observed["results"] == expected["results"]
