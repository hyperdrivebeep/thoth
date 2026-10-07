"""Measure how long the project list reads take as a project grows. No model is called.

Usage:
    python scripts/measure_project_lists.py [--scales 1,10,100] [--runs 5]

A temporary workspace is built for each scale (one unit is the demo size: 20 hypotheses, 60 test
results, 10 closures, 60 lessons, 5 threads) and each list is read `--runs` times through the query
route; the median time in milliseconds is printed as a table and as one JSON line. The records are
written straight into the ledger as the services would write them, so nothing here asks a model,
a person or a network. A list that stays under the threshold (300 ms) at the largest scale needs no
change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.application.services import request_records
from thoth.application.services.lesson_ledger import (
    LessonLedger,
    _dump,  # pyright: ignore[reportPrivateUsage]
)
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.trace_closure import TraceClosures
from thoth.application.services.trace_csv import export_csv
from thoth.apps.runtime import create_runtime
from thoth.domain.discrimination import DiscriminationLedgerRecord, DiscriminationResult
from thoth.domain.enums import EntityType, ThreadExecutionState, ThreadLifecycle
from thoth.domain.lesson_ref import (
    LessonRef,
    LessonReferenceRecord,
    LessonSource,
    fingerprint_of,
    lesson_id_of,
)
from thoth.domain.project import WorkThread
from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate
from thoth.domain.trace_closure import ClosureEvent, TraceClosureRecord
from thoth.domain.verification_trace import (
    Comparator,
    CriterionRule,
    TraceItem,
    TraceKind,
    TraceLink,
    TraceRelation,
    TraceSet,
)
from thoth.protocol.jsonrpc import JsonRpcRequest

THRESHOLD_MS = 300.0
DEMO = {"hypotheses": 20, "results": 60, "closures": 10, "lessons": 60, "threads": 5}
LISTS: tuple[tuple[str, dict[str, object]], ...] = (
    ("thread/list", {}),
    ("hypothesis/link/list", {}),
    ("trace/lesson/list", {}),
    ("hypothesis/test/result/list", {}),
    ("trace/closure/list", {}),
    ("trace/export", {}),
)
PROJECT = "p"
DIGEST = "d" * 64


def call(method: str, key: str, values: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {"id": key, "method": method, "params": {"_meta": {"idempotencyKey": key}, "input": values}}
    )


def trace_set(rows: int) -> TraceSet:
    """One requirement and `rows` criteria that each have a rule and no result (all open rows)."""
    items = [TraceItem(item_id="REQ-1", item_key="k-req", kind=TraceKind.REQUIREMENT, title="요구")]
    links: list[TraceLink] = []
    rules: list[CriterionRule] = []
    for number in range(rows):
        cid = f"C-{number}"
        items.append(
            TraceItem(item_id=cid, item_key=f"k-{number}", kind=TraceKind.CRITERION, title=cid)
        )
        links.append(
            TraceLink(
                link_id=f"L-{number}",
                from_id=cid,
                to_id="REQ-1",
                relation=TraceRelation.REFINES,
            )
        )
        rules.append(
            CriterionRule(
                rule_id=f"R-{number}",
                criterion_id=cid,
                measure="rate",
                comparator=Comparator.AT_LEAST,
                threshold=Decimal("0.9"),
                unit="ratio",
                condition="weather=rain",
            )
        )
    return TraceSet(items=tuple(items), links=tuple(links), rules=tuple(rules))


async def build(workspace: Path, scale: int) -> tuple[Any, dict[str, int]]:
    sizes = {name: size * scale for name, size in DEMO.items()}
    runtime = create_runtime(
        workspace,
        resource_scope_policy=ResourceScopePolicy(
            default=ResourceScopeTemplate(owner_kind="PROJECT", visibility="PROJECT_SHARED")
        ),
    )
    created = await runtime.bus.dispatch(
        call(
            "project/create",
            "project",
            {"project_id": PROJECT, "name": "measure", "cutoff_at": "2026-09-13T00:00:00Z"},
        )
    )
    assert created.error is None, created.error
    rows = max(sizes["closures"], 5)
    text = export_csv(trace_set(rows))
    params: dict[str, object] = {"project_id": PROJECT, "mode": "CREATE", "csv_text": text}
    preview = await runtime.bus.query(call("trace/importPreview", "preview", params))
    assert preview.error is None and preview.result is not None, preview.error
    plan = cast(dict[str, Any], preview.result["value"])
    applied = await runtime.bus.dispatch(
        call(
            "trace/importApply",
            "apply",
            {**params, "preview_id": plan["preview_id"], "input_sha256": plan["input_sha256"]},
        )
    )
    assert applied.error is None, applied.error
    records = RequestRecords(
        runtime.ledger,
        cast(Any, None),  # the write path used here never reads the control records
        SystemClock(),
        UuidIdGenerator(),
    )
    verdicts = TraceClosures(records)._reader.current_verdicts(  # pyright: ignore[reportPrivateUsage]
        PROJECT,
        TraceClosures(records)._reader.trace(PROJECT)[1],  # pyright: ignore[reportPrivateUsage]
    )
    rows_now = [(kind, subject) for (kind, subject) in verdicts if kind.value == "CRITERION"]
    now = datetime.now(UTC)
    # threads
    store = SqliteThreadStore(runtime.ledger.engine)
    for number in range(sizes["threads"]):
        store.create(
            WorkThread(
                thread_id=f"thread:{number}",
                project_id=PROJECT,
                cycle_id=f"cycle:{number}",
                problem=f"question {number}",
                lifecycle=ThreadLifecycle.OPEN,
                execution_state=ThreadExecutionState.IDLE,
                working_head_digest=DIGEST,
            )
        )
    # hypotheses, each from one open row (written the way the revision service writes any record)
    write_hypotheses(records, sizes["hypotheses"], rows_now, verdicts, now)
    # test results, spread over the hypotheses and their tests
    results = tuple(
        DiscriminationResult(
            event_id=f"e{number}",
            hypothesis_id=f"hypothesis:{number % sizes['hypotheses']}",
            hypothesis_revision_digest=DIGEST,
            test_id=f"test-{number // sizes['hypotheses']}",
            observation="what was seen",
            matched=("THIS_HYPOTHESIS", "ALTERNATIVE", "NEITHER")[number % 3],  # type: ignore[arg-type]
            actor_id="human:local-user",
            created_at=now,
        )
        for number in range(sizes["results"])
    )
    records.save(
        PROJECT,
        EntityType.THREAD,
        "hypothesis-discrimination",
        DiscriminationLedgerRecord(project_id=PROJECT, results=results),
        "human:local-user",
    )
    # closures, spread over the open rows
    closures: list[ClosureEvent] = []
    for number in range(sizes["closures"]):
        kind, subject = rows_now[number % len(rows_now)]
        current = verdicts[(kind, subject)]
        closures.append(
            ClosureEvent(
                event_id=f"c{number}",
                subject_kind="CRITERION",
                subject_id=subject,
                kind="HUMAN_CLOSED",
                basis_ref=f"doc-{number}",
                verdict_revision=current.verdict_revision,
                verdict_digest=current.verdict_digest,
                verdict_state=current.state,
                actor_id="human:local-user",
                created_at=now,
            )
        )
    records.save(
        PROJECT,
        EntityType.THREAD,
        "trace-closures",
        TraceClosureRecord(project_id=PROJECT, events=tuple(closures)),
        "human:local-user",
    )
    write_lessons(records, sizes["lessons"], results, tuple(closures), rows_now)
    return runtime, sizes


def write_hypotheses(
    records: RequestRecords,
    count: int,
    rows_now: list[tuple[Any, str]],
    verdicts: dict[Any, Any],
    now: datetime,
) -> None:
    from thoth.domain.hypothesis_full import HypothesisRecord
    from thoth.domain.verdict_link import VerdictLink

    # A hypothesis is not a request record: it has the 1.0.0 snapshot digest and needs no codec.
    original = request_records.decode_research_record, request_records.domain_digest
    digest = request_records.domain_digest

    def no_codec(content: dict[str, object]) -> None:
        return None

    def old_digest(kind: str, version: str, payload: bytes) -> str:
        return digest(kind, "1.0.0" if kind == "SNAPSHOT" else version, payload)

    request_records.decode_research_record = cast(Any, no_codec)
    request_records.domain_digest = old_digest
    try:
        for number in range(count):
            kind, subject = rows_now[number % len(rows_now)]
            current = verdicts[(kind, subject)]
            record = HypothesisRecord(
                hypothesis_revision_id=f"revision:{number}",
                hypothesis_id=f"hypothesis:{number}",
                project_id=PROJECT,
                object_id="object:1",
                portfolio_id="portfolio:1",
                statement=f"hypothesis {number}",
                observed_problem="observed",
                primary_intent=None,
                evidence_basis="basis",
                scope={},
                evidence_refs=(),
                revision_digest=DIGEST,
                created_at=now,
                verdict_link=VerdictLink(
                    subject_kind="CRITERION",
                    subject_id=subject,
                    verdict_revision=current.verdict_revision,
                    verdict_digest=current.verdict_digest,
                    state=current.state,
                ),
            )
            records.save(
                PROJECT, EntityType.HYPOTHESIS, record.hypothesis_id, record, "agent:research"
            )
    finally:
        request_records.decode_research_record, request_records.domain_digest = original


def write_lessons(
    records: RequestRecords,
    count: int,
    results: tuple[DiscriminationResult, ...],
    closures: tuple[ClosureEvent, ...],
    rows_now: list[tuple[Any, str]],
) -> None:
    ledger = LessonLedger(records)
    now = datetime.now(UTC)
    refs: list[LessonRef] = []
    shown = ledger._reader.current_verdicts(  # pyright: ignore[reportPrivateUsage]
        PROJECT,
        ledger._reader.trace(PROJECT)[1],  # pyright: ignore[reportPrivateUsage]
    )
    for number in range(count):
        kind, subject = rows_now[number % len(rows_now)]
        found = ledger.row_now(PROJECT, kind.value, subject, shown)
        if found is None:
            continue
        context, digest = found
        if number % 5 == 4 and closures:
            event = closures[number % len(closures)]
            source = [LessonSource(record_kind="TRACE_CLOSURE", record_id=event.event_id)]
            refs.append(
                LessonRef(
                    lesson_id=lesson_id_of("HUMAN_CLOSURE", "HUMAN_CLOSED", source) + f"-{number}",
                    kind="HUMAN_CLOSURE",
                    outcome="HUMAN_CLOSED",
                    sources=tuple(source),
                    fingerprint=fingerprint_of({"event": _dump(event), "verdict_digest": digest}),
                    context=context,
                    actor_id="human:local-user",
                    created_at=now,
                )
            )
            continue
        event = results[number % len(results)]
        source = [LessonSource(record_kind="DISCRIMINATION_RESULT", record_id=event.event_id)]
        refs.append(
            LessonRef(
                lesson_id=lesson_id_of("TEST_RESULT", event.matched, source) + f"-{number}",
                kind="TEST_RESULT",
                outcome=event.matched,
                sources=tuple(source),
                fingerprint=fingerprint_of({"event": _dump(event)}),
                context=context,
                hypothesis_id=event.hypothesis_id,
                test_id=event.test_id,
                actor_id="human:local-user",
                created_at=now,
            )
        )
    records.save(
        PROJECT,
        EntityType.THREAD,
        "lesson-references",
        LessonReferenceRecord(project_id=PROJECT, refs=tuple(refs)),
        "system:lesson-rule",
    )


async def measure(runtime: Any, runs: int) -> dict[str, float]:
    found: dict[str, float] = {}
    for method, values in LISTS:
        times: list[float] = []
        for number in range(runs):
            begun = time.perf_counter()
            response = await runtime.bus.query(
                call(method, f"{method}-{number}", {"project_id": PROJECT, **values})
            )
            times.append((time.perf_counter() - begun) * 1000)
            assert response.error is None, (method, response.error)
        found[method] = round(statistics.median(times), 1)
    return found


async def main_async(scales: list[int], runs: int) -> dict[str, dict[str, float]]:
    table: dict[str, dict[str, float]] = {}
    for scale in scales:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            built = time.perf_counter()
            runtime, sizes = await build(Path(directory), scale)
            try:
                table[f"x{scale} {json.dumps(sizes, sort_keys=True)}"] = await measure(
                    runtime, runs
                )
            finally:
                runtime.close()
            print(f"scale {scale}: built in {time.perf_counter() - built:.1f}s", file=sys.stderr)
    return table


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", default="1,10,100")
    parser.add_argument("--runs", type=int, default=5)
    arguments = parser.parse_args(argv)
    table = asyncio.run(
        main_async([int(item) for item in arguments.scales.split(",")], arguments.runs)
    )
    names = [method for method, _ in LISTS]
    print("| scale (sizes) | " + " | ".join(names) + " |")
    print("|---|" + "---|" * len(names))
    for label, row in table.items():
        print(f"| {label} | " + " | ".join(f"{row[name]:.1f}" for name in names) + " |")
    print(json.dumps({"median_ms": table, "threshold_ms": THRESHOLD_MS}, ensure_ascii=False))
    worst = max(max(row.values()) for row in table.values())
    print(f"slowest median: {worst:.1f} ms (threshold {THRESHOLD_MS:.0f} ms)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
