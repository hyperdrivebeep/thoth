"""The project list reads do not repeat the same read for every item, and answer what they did.

The lesson list used to read the whole trace again for each lesson that rests on a row (so its time
grew with lessons times rows). It now reads the verdicts once per request. These tests count the
reads and compare the answer with the one the per-lesson path gives. No model is called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.test_hypothesis_link import change_result, investigate
from tests.integration.test_judgment_records import close, only_hypothesis, record
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_trace_origin import FOG, RAIN, opened

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.application.services.hypothesis_link_view import HypothesisLinkReader
from thoth.application.services.hypothesis_same import read_same
from thoth.application.services.lesson_ledger import LessonLedger
from thoth.application.services.request_records import RequestRecords
from thoth.application.services.trace_closure import TraceClosures
from thoth.application.services.verification_trace import REVISION_PREFIX
from thoth.domain.hypothesis_same import groups
from thoth.domain.lesson_ref import recall_lessons


def reference_recall(ledger: LessonLedger, project_id: str, rows: Any) -> list[dict[str, object]]:
    """The answer the per-lesson path gives: each lesson looks the trace up for itself."""
    refs = ledger.read(project_id).refs
    results = ledger._results(project_id)  # pyright: ignore[reportPrivateUsage]
    events = {item.event_id: item for item in ledger._closure_events(project_id)}  # pyright: ignore[reportPrivateUsage]
    by_row = {(str(item["subject_kind"]), str(item["subject_id"])): item for item in rows}
    prints = {
        item.lesson_id: ledger._fingerprint_now(project_id, item, results, events, by_row)  # pyright: ignore[reportPrivateUsage]
        for item in refs
    }
    reader = ledger._reader  # pyright: ignore[reportPrivateUsage]
    siblings = {
        member: group for group in groups(read_same(reader, project_id)) for member in group
    }
    found: list[dict[str, object]] = []
    for kind, subject_id in dict.fromkeys(
        (r.context.subject_kind, r.context.subject_id) for r in refs
    ):
        now = ledger.row_now(project_id, kind, subject_id)
        if now is None:
            continue
        for ref, state in recall_lessons(refs, now[0], prints, siblings):
            if (ref.context.subject_kind, ref.context.subject_id) == (kind, subject_id):
                item = ledger._item(ref, state, {i.event_id: i for i in results}, events)  # pyright: ignore[reportPrivateUsage]
                others = siblings.get(ref.hypothesis_id or "", frozenset()) - {ref.hypothesis_id}
                found.append({**item, "same_hypothesis_ids": sorted(others)} if others else item)
    return found


@pytest.mark.asyncio
async def test_the_lesson_list_reads_the_verdicts_once_and_answers_what_the_per_lesson_path_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await rpc.rain_arrives()
        await investigate(runtime, rpc, RAIN)
        hypothesis_id = await only_hypothesis(rpc)
        await record(rpc, hypothesis_id, "test-1", "ALTERNATIVE")
        await record(rpc, hypothesis_id, "test-2", "THIS_HYPOTHESIS")
        await record(rpc, hypothesis_id, "test-2", "ALTERNATIVE")  # the earlier result is replaced
        await close(rpc, RAIN, "HUMAN_CLOSED", basis_ref="minutes-1")
        await close(rpc, RAIN, "WAIVER_RECORDED", basis_ref="deviation-4")
        await close(rpc, FOG, "HUMAN_CLOSED", basis_ref="minutes-2")
        await close(rpc, FOG, "FIX_APPLIED", basis_ref="ECN-1")
        await change_result(rpc, "SYN-RES-SYN-C-DET-RAIN", phase=2, value="0.95", numerator="19")
        records = RequestRecords(runtime.ledger, cast(Any, None), SystemClock(), UuidIdGenerator())
        ledger, closures = LessonLedger(records), TraceClosures(records)
        rows = closures.view("p")
        reads: list[str] = []
        original = HypothesisLinkReader.current_verdicts

        def counted(self: HypothesisLinkReader, project_id: str, trace: Any, *rest: Any) -> Any:
            reads.append(project_id)
            return original(self, project_id, trace, *rest)

        monkeypatch.setattr(HypothesisLinkReader, "current_verdicts", counted)
        answer = ledger.recall("p", rows)
        assert len(reads) == 1  # not once per lesson
        monkeypatch.setattr(HypothesisLinkReader, "current_verdicts", original)
        assert len(answer) >= 6  # results, eliminations, closures and an effect, in several states
        assert {item["state"] for item in answer} >= {"SAME_CONDITION", "STALE"}
        assert answer == reference_recall(ledger, "p", rows)
        assert (await rpc("trace/lesson/list", project_id="p"))["lessons"] == answer
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_csv_export_notes_closures_without_reading_every_rows_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        await close(rpc, FOG, "HUMAN_CLOSED", basis_ref="minutes-2")
        await close(rpc, FOG, "FIX_APPLIED", basis_ref="ECN-1")
        listed = (await rpc("trace/closure/list", project_id="p"))["closures"]
        reads: list[str] = []
        original = HypothesisLinkReader.current_verdicts

        def counted(self: HypothesisLinkReader, project_id: str, trace: Any, *rest: Any) -> Any:
            reads.append(project_id)
            return original(self, project_id, trace, *rest)

        monkeypatch.setattr(HypothesisLinkReader, "current_verdicts", counted)
        text = (await rpc("trace/export", project_id="p"))["csv_text"]
        # the notes need the records and the confirmed effect, not each row's verdict
        assert reads == []
        monkeypatch.setattr(HypothesisLinkReader, "current_verdicts", original)
        assert "이전 기록 1건(THOTH에서 확인)" in text and "수정 반영됨(효과 미확인)" in text
        # the note is the same as the one the full view gives
        from thoth.application.services.trace_closure_text import closure_status_cells

        records = RequestRecords(runtime.ledger, cast(Any, None), SystemClock(), UuidIdGenerator())
        closures = TraceClosures(records)
        full = closure_status_cells(closures.view("p"))
        assert full == closure_status_cells(closures.view("p", with_current=False))
        assert all(note in text for note in full.values()) and len(listed) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_list_reads_the_heads_a_fixed_number_of_times_however_many_records_it_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel(one=True, tested=True))
    try:
        await rpc.rain_arrives()
        await investigate(runtime, rpc, RAIN)
        hypothesis_id = await only_hypothesis(rpc)
        await record(rpc, hypothesis_id, "test-1", "ALTERNATIVE")
        await close(rpc, RAIN, "HUMAN_CLOSED", basis_ref="minutes-rain")
        records = RequestRecords(runtime.ledger, cast(Any, None), SystemClock(), UuidIdGenerator())
        ledger, closures = LessonLedger(records), TraceClosures(records)
        calls: list[str] = []
        original = type(runtime.ledger).read_heads

        def counted(self: Any, project_id: str) -> Any:
            calls.append(project_id)
            return original(self, project_id)

        monkeypatch.setattr(type(runtime.ledger), "read_heads", counted)

        def reads() -> tuple[int, int, int]:
            """Reads of the heads for the closure list, the lesson list and the link list."""
            calls.clear()
            rows = closures.view("p")
            first = len(calls)
            calls.clear()
            ledger.recall("p", rows)
            second = len(calls)
            calls.clear()
            HypothesisLinkReader(runtime.ledger).items("p")
            return first, second, len(calls)

        few = reads()
        # more results, more closures and more lessons on more rows change nothing about the reads
        await record(rpc, hypothesis_id, "test-2", "THIS_HYPOTHESIS")
        await record(rpc, hypothesis_id, "test-1", "NEITHER")
        for number in range(3):
            await close(rpc, FOG, "HUMAN_CLOSED", basis_ref=f"minutes-fog-{number}")
        many = reads()
        assert many == few, (few, many)
        assert few[0] <= 3 and few[1] <= 8, few
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_list_reads_the_verdicts_of_the_rows_it_shows_not_of_every_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, rpc = await opened(tmp_path, ControlledResearchModel())
    try:
        await close(rpc, FOG, "HUMAN_CLOSED", basis_ref="minutes-fog")  # one closed row of several
        records = RequestRecords(runtime.ledger, cast(Any, None), SystemClock(), UuidIdGenerator())
        closures = TraceClosures(records)
        every_row = len(closures._reader.trace("p")[1].current_digests())  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
        read: list[str] = []
        original = HypothesisLinkReader.content

        def counted(
            self: HypothesisLinkReader, project_id: str, kind: Any, entity_id: str, *rest: Any
        ) -> Any:
            if entity_id.startswith(REVISION_PREFIX):
                read.append(entity_id)  # the stored verdict of one row
            return original(self, project_id, kind, entity_id, *rest)

        monkeypatch.setattr(HypothesisLinkReader, "content", counted)
        listed = closures.view("p")
        assert [item["subject_id"] for item in listed] == [FOG]
        assert every_row > 2 and len(read) == 1, (len(read), every_row)
    finally:
        runtime.close()
