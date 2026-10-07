"""The trace CSV through the public methods: export, preview, apply, and what must not happen."""

from __future__ import annotations

import csv
import io
import json
import random
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup
from tests.integration.trace_demo_helpers import NOTICE, REQ, demo_set, with_notice

from thoth.application.services import verification_trace as trace_module
from thoth.application.services.trace_csv import (
    BOM,
    FORMAT,
    export_csv,
    export_rows,
    write_csv,
)
from thoth.apps.runtime import AppRuntime
from thoth.protocol.bus import READ_QUERY_METHODS
from thoth.protocol.jsonrpc import JsonRpcResponse

FIXTURES = Path(__file__).parents[1] / "fixtures" / "trace_csv"


class Rpc:
    """Calls the way a client does: reads are queries, changes are commands with a key."""

    def __init__(self, runtime: AppRuntime) -> None:
        self.runtime, self.count = runtime, 0

    async def raw(self, method: str, **params: Any) -> JsonRpcResponse:
        self.count += 1
        message = request(method, f"k{self.count}", params)
        if method in READ_QUERY_METHODS:
            return await self.runtime.bus.query(message)
        return await self.runtime.bus.dispatch(message)

    async def __call__(self, method: str, **params: Any) -> dict[str, Any]:
        return value(await self.raw(method, **params))

    async def error(self, method: str, **params: Any) -> str:
        response = await self.raw(method, **params)
        assert response.error is not None, "expected an error"
        return response.error.message

    async def project(self, name: str) -> str:
        await self(
            "project/create",
            project_id=name,
            name=name,
            cutoff_at="2026-09-13T00:00:00Z",
        )
        return name

    async def load(self, project: str, text: str, mode: str = "CREATE") -> dict[str, Any]:
        """Preview, then apply exactly what was previewed."""
        preview = await self("trace/importPreview", project_id=project, mode=mode, csv_text=text)
        assert preview["applicable"], preview["conflicts"]
        return await self(
            "trace/importApply",
            project_id=project,
            mode=mode,
            csv_text=text,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )


async def opened(tmp_path: Path) -> tuple[AppRuntime, Rpc]:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    return runtime, Rpc(runtime)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_bytes().decode("utf-8")


def table(text: str) -> tuple[list[str], list[list[str]]]:
    lines = list(csv.reader(io.StringIO(text.removeprefix(BOM), newline="")))
    return lines[0], lines[1:]


def rebuild(
    header: list[str], rows: list[list[str]], *, eol: str = "\r\n", bom: bool = True
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=eol)
    writer.writerow(header)
    writer.writerows(rows)
    return (BOM if bom else "") + buffer.getvalue()


def changes(preview: dict[str, Any]) -> tuple[int, int, int]:
    found = preview["changes"]
    return len(found["added"]), len(found["updated"]), len(found["deleted"])


def row_key(row: dict[str, str]) -> tuple[str, str]:
    return (row["row_type"], row.get("item_id") or row.get("link_id") or row.get("result_id", ""))


async def stored_trace_bytes(runtime: AppRuntime, project: str) -> int:
    """Size of the trace record as the ledger stored it last (not of the whole ledger)."""
    digest = runtime.ledger.read_heads(project)["THREAD:verification-trace:project"]
    revision = runtime.ledger.read_revision_by_digest(project, digest)
    assert revision is not None
    snapshot = runtime.ledger.read_snapshot(revision.snapshot_id)
    assert snapshot is not None
    return len(json.dumps(dict(snapshot.content), default=str))


def codes(preview: dict[str, Any]) -> set[str]:
    return {item["code"] for item in preview["conflicts"]}


# --- round trip ---


@pytest.mark.asyncio
async def test_an_exported_file_imports_back_with_no_change(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        await rpc.load(await rpc.project("a"), export_csv(demo_set(2)))
        exported = await rpc("trace/export", project_id="a")
        text = exported["csv_text"]
        assert text.startswith(BOM + "format,row_type,base_set_digest")
        assert exported["format"] == FORMAT and exported["loss_manifest"]
        preview = await rpc("trace/importPreview", project_id="a", mode="UPDATE", csv_text=text)
        assert preview["applicable"] and changes(preview) == (0, 0, 0)
        assert preview["changes"]["unchanged"] > 0
        applied = await rpc.load("a", text, "UPDATE")
        assert applied["applied"] is False and applied["reason"] == "NO_CHANGES"
        assert (await rpc("trace/read", project_id="a"))["set_digest"] == exported["set_digest"]
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_row_and_column_order_blank_lines_and_the_byte_order_mark_do_not_matter(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        await rpc.load(await rpc.project("a"), export_csv(demo_set(2)))
        header, rows = table((await rpc("trace/export", project_id="a"))["csv_text"])
        generator = random.Random(7)
        columns = list(range(len(header)))
        generator.shuffle(columns)
        shuffled_header = [header[at] for at in columns]
        shuffled = [[row[at] for at in columns] for row in rows]
        generator.shuffle(shuffled)
        padded: list[list[str]] = []
        for at, row in enumerate(shuffled):
            padded.append(row)
            if at % 3 == 0:
                padded.append([])
                padded.append([""] * len(header))
        variants = {
            "shuffled": rebuild(shuffled_header, padded),
            "no mark": rebuild(shuffled_header, padded, bom=False),
            "lf only": rebuild(header, rows, eol="\n"),
            "ordinary": rebuild(header, rows),
        }
        for name, text in variants.items():
            preview = await rpc("trace/importPreview", project_id="a", mode="UPDATE", csv_text=text)
            assert preview["applicable"], (name, preview["conflicts"])
            assert changes(preview) == (0, 0, 0), name
    finally:
        runtime.close()


# --- exchange fixtures ---


@pytest.mark.asyncio
async def test_ids_with_leading_zeros_long_digits_and_date_shapes_are_kept_exactly(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        expected = {
            "ids_leading_zero.csv": {"007", "0012", "00"},
            "ids_long_numeric.csv": {"12345678901234567890", "1234567890123456789012", "1.5E+3"},
            "ids_date_like.csv": {"1-2", "2026-10", "3/4"},
        }
        for number, (name, ids) in enumerate(expected.items()):
            project = await rpc.project(f"ids{number}")
            await rpc.load(project, fixture(name))
            first = await rpc("trace/read", project_id=project)
            assert {item["item_id"] for item in first["items"]} == ids
            again = await rpc("trace/export", project_id=project)
            preview = await rpc(
                "trace/importPreview", project_id=project, mode="UPDATE", csv_text=again["csv_text"]
            )
            assert changes(preview) == (0, 0, 0) and preview["applicable"], name
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_text_that_starts_like_a_formula_is_written_safely_and_read_back_exactly(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        project = await rpc.project("formula")
        await rpc.load(project, fixture("formula_prefix_escaped.csv"))
        titles = {
            item["item_id"]: item["title"]
            for item in (await rpc("trace/read", project_id=project))["items"]
        }
        assert titles == {
            "F-1": "=1+1",
            "F-2": "+SUM(A1)",
            "F-3": "-2+3",
            "F-4": "@cmd",
            "F-5": "\tTab first",
            "F-6": "\rCR first",
            "F-7": "'quote that is real text",
            "F-8": "'=two apostrophes then formula",
        }
        text = (await rpc("trace/export", project_id=project))["csv_text"]
        _, rows = table(text)
        written = {row[3]: row[11] for row in rows}
        assert all(
            cell.startswith("'")
            for key, cell in written.items()
            if key in {"F-1", "F-2", "F-3", "F-4", "F-5", "F-6"}
        )
        assert written["F-7"] == "'quote that is real text"  # not a formula, left as it was
        assert written["F-8"] == "''=two apostrophes then formula"
        preview = await rpc("trace/importPreview", project_id=project, mode="UPDATE", csv_text=text)
        assert changes(preview) == (0, 0, 0)
        raw = await rpc.project("rawformula")
        await rpc.load(raw, fixture("formula_prefix_raw.csv"))  # typed without the apostrophe
        items = (await rpc("trace/read", project_id=raw))["items"]
        assert {item["title"] for item in items} >= {"=1+1", "@cmd"}
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_korean_text_quotes_commas_and_line_breaks_survive(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        project = await rpc.project("korean")
        await rpc.load(project, fixture("korean_special.csv"))
        titles = {
            item["item_id"]: item["title"]
            for item in (await rpc("trace/read", project_id=project))["items"]
        }
        assert titles["REQ-한글-1"] == "탐지율은 0.90 이상, 비 오는 날 포함"
        assert titles["REQ-2"] == '따옴표 "안의" 글자와 쉼표, 그리고 줄바꿈\n두 번째 줄'
        again = (await rpc("trace/export", project_id=project))["csv_text"]
        preview = await rpc(
            "trace/importPreview", project_id=project, mode="UPDATE", csv_text=again
        )
        assert changes(preview) == (0, 0, 0)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_same_content_with_or_without_a_mark_or_in_another_order_is_the_same_trace(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        digests: set[str] = set()
        for number, name in enumerate(
            ("order_baseline.csv", "bom_utf8.csv", "no_bom_utf8.csv", "order_shuffled.csv")
        ):
            project = await rpc.project(f"same{number}")
            await rpc.load(project, fixture(name))
            digests.add((await rpc("trace/read", project_id=project))["set_digest"])
        assert len(digests) == 1
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_zero_blank_null_and_quoted_values_are_told_apart(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        project = await rpc.project("zeros")
        await rpc.load(project, fixture("zero_empty_null.csv"))
        results = {
            item["result_id"]: item
            for item in (await rpc("trace/read", project_id=project))["results"]
        }
        assert (results["Z-1"]["value"], results["Z-1"]["raw_value"]) == ("0", None)
        assert (results["Z-2"]["value"], results["Z-2"]["raw_value"]) == (
            "0",
            None,
        )  # quotes are CSV syntax
        assert (results["Z-3"]["value"], results["Z-3"]["raw_value"]) == (None, None)
        assert (results["Z-4"]["value"], results["Z-4"]["raw_value"]) == (None, "null")
        assert (results["Z-5"]["value"], results["Z-5"]["raw_value"]) == (None, None)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_repeated_id_is_a_conflict_and_nothing_is_saved(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        project = await rpc.project("dup")
        text = fixture("ids_duplicate.csv")
        preview = await rpc("trace/importPreview", project_id=project, mode="CREATE", csv_text=text)
        assert not preview["applicable"] and "DUPLICATE_ID_IN_FILE" in codes(preview)
        message = await rpc.error(
            "trace/importApply",
            project_id=project,
            mode="CREATE",
            csv_text=text,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )
        assert "TRACE_IMPORT_NOT_APPLICABLE" in message
        assert (await rpc("trace/read", project_id=project))["record_digest"] is None
    finally:
        runtime.close()


# --- when an import is refused ---


@pytest.mark.asyncio
async def test_a_file_made_from_another_revision_is_refused_as_stale_base(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        await rpc.load(await rpc.project("a"), export_csv(demo_set(1)))
        old = (await rpc("trace/export", project_id="a"))["csv_text"]
        header, rows = table(old)
        extra = [*rows[0]]
        extra[header.index("item_id")] = "SYN-NEW"
        extra[header.index("item_key")] = "k-new"
        extra[header.index("title")] = "added later"
        await rpc.load("a", rebuild(header, [*rows, extra]), "UPDATE")
        preview = await rpc("trace/importPreview", project_id="a", mode="UPDATE", csv_text=old)
        assert not preview["applicable"] and codes(preview) == {"STALE_BASE"}
        message = await rpc.error(
            "trace/importApply",
            project_id="a",
            mode="UPDATE",
            csv_text=old,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )
        assert "STALE_BASE" in message
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_rows_that_are_missing_from_the_file_are_not_deletes(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        await rpc.load(await rpc.project("a"), export_csv(demo_set(2)))
        header, rows = table((await rpc("trace/export", project_id="a"))["csv_text"])
        before = await rpc("trace/read", project_id="a")
        preview = await rpc(
            "trace/importPreview", project_id="a", mode="UPDATE", csv_text=rebuild(header, rows[:2])
        )
        assert preview["applicable"] and changes(preview) == (0, 0, 0)
        applied = await rpc.load("a", rebuild(header, rows[:2]), "UPDATE")
        assert applied["applied"] is False
        after = await rpc("trace/read", project_id="a")
        assert (after["set_digest"], len(after["items"])) == (
            before["set_digest"],
            len(before["items"]),
        )
    finally:
        runtime.close()


def delete_row(header: list[str], digest: str, kind: str, target: str) -> list[str]:
    row = [""] * len(header)
    row[header.index("format")] = FORMAT
    row[header.index("row_type")] = "DELETE"
    row[header.index("base_set_digest")] = digest
    row[header.index("target_kind")] = kind
    row[header.index("target_id")] = target
    return row


@pytest.mark.asyncio
async def test_an_item_that_is_still_linked_cannot_be_deleted_but_a_clean_delete_works(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        await rpc.load(await rpc.project("a"), fixture("order_baseline.csv"))
        exported = await rpc("trace/export", project_id="a")
        header, _ = table(exported["csv_text"])
        digest = exported["set_digest"]
        blocked = rebuild(header, [delete_row(header, digest, "ITEM", "TC-1")])
        preview = await rpc("trace/importPreview", project_id="a", mode="UPDATE", csv_text=blocked)
        assert not preview["applicable"] and codes(preview) == {"DELETE_BLOCKED_BY_LINK"}
        message = await rpc.error(
            "trace/importApply",
            project_id="a",
            mode="UPDATE",
            csv_text=blocked,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )
        assert "DELETE_BLOCKED_BY_LINK" in message
        assert (await rpc("trace/read", project_id="a"))["set_digest"] == digest
        clean = rebuild(
            header,
            [
                delete_row(header, digest, "LINK", "L-2"),
                delete_row(header, digest, "ITEM", "TC-1"),
            ],
        )
        applied = await rpc.load("a", clean, "UPDATE")
        assert applied["counts"]["deleted"] == 2
        left = await rpc("trace/read", project_id="a")
        assert {item["item_id"] for item in left["items"]} == {"REQ-1", "CRIT-1"}
        assert {item["link_id"] for item in left["links"]} == {"L-1"}
        missing = rebuild(header, [delete_row(header, left["set_digest"], "ITEM", "NOPE")])
        gone = await rpc("trace/importPreview", project_id="a", mode="UPDATE", csv_text=missing)
        assert codes(gone) == {"DELETE_TARGET_NOT_FOUND"}
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_bad_row_or_a_failure_while_applying_saves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        await rpc.load(await rpc.project("a"), fixture("order_baseline.csv"))
        exported = await rpc("trace/export", project_id="a")
        header, rows = table(exported["csv_text"])
        before = await rpc("trace/read", project_id="a")
        good = [*rows[0]]
        good[header.index("item_id")], good[header.index("item_key")] = "NEW-1", "k-new-1"
        bad = [*rows[5]]  # the rule row, with a threshold that is not a number
        bad[header.index("threshold")] = "abc"
        text = rebuild(header, [good, bad])
        preview = await rpc("trace/importPreview", project_id="a", mode="UPDATE", csv_text=text)
        assert not preview["applicable"] and codes(preview) == {"ROW_INVALID"}
        assert "TRACE_IMPORT_NOT_APPLICABLE" in await rpc.error(
            "trace/importApply",
            project_id="a",
            mode="UPDATE",
            csv_text=text,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )
        assert (await rpc("trace/read", project_id="a"))["record_digest"] == before["record_digest"]

        # A good file whose verdict step fails: the new set must not be stored either.
        text = rebuild(header, [good])
        preview = await rpc("trace/importPreview", project_id="a", mode="UPDATE", csv_text=text)
        assert preview["applicable"] and changes(preview) == (1, 0, 0)

        def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("verdict step failed")

        monkeypatch.setattr(trace_module, "compute_verdicts", fail)
        response = await rpc.raw(
            "trace/importApply",
            project_id="a",
            mode="UPDATE",
            csv_text=text,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )
        assert response.error is not None
        monkeypatch.undo()
        after = await rpc("trace/read", project_id="a")
        assert after["record_digest"] == before["record_digest"] and len(after["items"]) == 3
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_an_apply_is_refused_when_the_trace_changed_after_the_preview(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        await rpc.load(await rpc.project("a"), fixture("order_baseline.csv"))
        exported = await rpc("trace/export", project_id="a")
        header, rows = table(exported["csv_text"])

        def added(name: str) -> str:
            row = [*rows[0]]
            row[header.index("item_id")], row[header.index("item_key")] = name, "k-" + name
            return rebuild(header, [row])

        first, second = added("ONE"), added("TWO")
        preview = await rpc("trace/importPreview", project_id="a", mode="UPDATE", csv_text=first)
        await rpc.load("a", second, "UPDATE")  # somebody else changed the trace meanwhile
        message = await rpc.error(
            "trace/importApply",
            project_id="a",
            mode="UPDATE",
            csv_text=first,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )
        assert "TRACE_IMPORT_PREVIEW_STALE" in message
        other = await rpc.error(
            "trace/importApply",
            project_id="a",
            mode="UPDATE",
            csv_text=second,
            preview_id=preview["preview_id"],
            input_sha256=preview["input_sha256"],
        )
        assert "TRACE_IMPORT_INPUT_CHANGED" in other
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_ids_that_look_rewritten_by_a_spreadsheet_are_conflicts(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        cases = {
            "ids_leading_zero.csv": ("007", "7", "LEADING_ZEROS_LOST"),
            "ids_date_like.csv": ("1-2", "2-Jan", "DATE_FORM"),
            "ids_long_numeric.csv": ("12345678901234567890", "1.23457E+19", "EXPONENT_FORM"),
        }
        for number, (name, (original, mangled, reason)) in enumerate(cases.items()):
            project = await rpc.project(f"x{number}")
            await rpc.load(project, fixture(name))
            exported = await rpc("trace/export", project_id=project)
            header, rows = table(exported["csv_text"])
            at = header.index("item_id")
            for row in rows:
                if row[at] == original:
                    row[at] = mangled
            preview = await rpc(
                "trace/importPreview",
                project_id=project,
                mode="UPDATE",
                csv_text=rebuild(header, rows),
            )
            assert not preview["applicable"], name
            suspect = preview["spreadsheet_suspects"][0]
            assert (
                suspect["ref"] == mangled
                and original in suspect["detail"]
                and reason in suspect["detail"]
            )
        # An id that is simply new is not a suspect while the original is still in the file.
        project = await rpc.project("fine")
        await rpc.load(project, fixture("ids_leading_zero.csv"))
        exported = await rpc("trace/export", project_id=project)
        header, rows = table(exported["csv_text"])
        extra = [*rows[0]]
        extra[header.index("item_id")], extra[header.index("item_key")] = "7", "k-seven"
        preview = await rpc(
            "trace/importPreview",
            project_id=project,
            mode="UPDATE",
            csv_text=rebuild(header, [*rows, extra]),
        )
        assert preview["applicable"] and changes(preview) == (1, 0, 0)
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_create_needs_an_empty_project_and_update_needs_a_trace(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        text = fixture("order_baseline.csv")
        empty = await rpc.project("empty")
        update = await rpc("trace/importPreview", project_id=empty, mode="UPDATE", csv_text=text)
        assert codes(update) == {"UPDATE_NEEDS_EXISTING_TRACE"}
        await rpc.load(empty, text)
        create = await rpc("trace/importPreview", project_id=empty, mode="CREATE", csv_text=text)
        assert codes(create) == {"MODE_CREATE_NEEDS_EMPTY_PROJECT"}
        missing = await rpc.raw("trace/read", project_id="missing")
        assert missing.error is not None and "not found" in missing.error.message
        columns = await rpc(
            "trace/importPreview", project_id=empty, mode="UPDATE", csv_text="a,b\n1,2\n"
        )
        assert "HEADER_MISSING_COLUMNS" in codes(columns)
    finally:
        runtime.close()


# --- the demo: one condition at a time ---


@pytest.mark.asyncio
async def test_demo_flow_create_dry_then_import_the_rain_results(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        first_set, second_set = demo_set(1), demo_set(2)
        created = await rpc.load(await rpc.project("radar"), export_csv(first_set))
        assert created["applied"] is True
        view = await rpc("trace/read", project_id="radar")
        state = {(v["subject_kind"], v["subject_id"]): v for v in view["verdicts"]}
        assert state[("REQUIREMENT", REQ)]["state"] == "HOLD_INCOMPLETE"
        assert state[("CRITERION", "SYN-C-DET-RAIN")]["state"] == "HOLD_NO_RESULT"
        assert state[("CRITERION", "SYN-C-DET-DRY")]["state"] == "PASS_COMPUTED"
        assert all(v["currentness"]["state"] == "CURRENT" for v in view["verdicts"])
        dry_before = state[("CRITERION", "SYN-C-DET-DRY")]["revision_digest"]
        rain_hold = state[("CRITERION", "SYN-C-DET-RAIN")]["revision_digest"]
        requirement_hold = state[("REQUIREMENT", REQ)]["revision_digest"]
        confirmed = await rpc(
            "trace/confirm",
            project_id="radar",
            verdict_revision_digest=requirement_hold,
            rationale="1단계 결과 확인",
            expected_digest=view["record_digest"],
        )
        assert [
            v["state"] for v in confirmed["verdicts"] if v["subject_kind"] == "REQUIREMENT"
        ] == ["HOLD_INCOMPLETE"]

        # The rain results as their own file: only the rows that are new, made from the dry trace.
        old_keys = {row_key(row) for row in export_rows(first_set)}
        keep = [row for row in export_rows(second_set) if row_key(row) not in old_keys]
        for row in keep:
            row["base_set_digest"] = first_set.set_digest
        rain_file = write_csv(keep)
        assert {row["row_type"] for row in keep} == {"ITEM", "LINK", "RESULT"} and len(keep) == 6
        preview = await rpc(
            "trace/importPreview", project_id="radar", mode="UPDATE", csv_text=rain_file
        )
        assert preview["applicable"] and changes(preview) == (6, 0, 0)
        applied = await rpc.load("radar", rain_file, "UPDATE")
        moved = {
            item["subject_id"]: (item["before"], item["after"])
            for item in applied["verdict_changes"]
        }
        assert moved == {
            "SYN-C-DET-RAIN": ("HOLD_NO_RESULT", "FAIL_COMPUTED"),
            "SYN-C-FA-RAIN": ("HOLD_NO_RESULT", "FAIL_COMPUTED"),
            REQ: ("HOLD_INCOMPLETE", "FAIL_WITH_INCOMPLETE_COVERAGE"),
        }

        view = await rpc("trace/read", project_id="radar")
        state = {(v["subject_kind"], v["subject_id"]): v for v in view["verdicts"]}
        assert state[("CRITERION", "SYN-C-DET-DRY")]["revision_digest"] == dry_before
        assert state[("CRITERION", "SYN-C-DET-FOG")]["state"] == "HOLD_NO_RESULT"
        rain = state[("CRITERION", "SYN-C-DET-RAIN")]
        assert rain["parent_digest"] == rain_hold and rain["cause"]["trigger"] == "CSV_IMPORT"
        assert {item["ref_id"] for item in rain["cause"]["changed"]} == {"SYN-RES-SYN-C-DET-RAIN"}
        assert [item["after"] for item in rain["cause"]["changed"]] == ["0.80 ratio @weather=rain"]
        assert all(v["currentness"]["state"] == "CURRENT" for v in view["verdicts"])
        requirement = state[("REQUIREMENT", REQ)]
        assert (
            requirement["confirmations"] == []
        )  # the 1st-phase confirmation stayed on its revision
        assert requirement["parent_digest"] == requirement_hold
        assert "history" not in view  # the full history is not part of trace/read any more
        assert requirement["history_total"] == 2 and rain["history_total"] == 2
        assert [h["revision_digest"] for h in rain["recent_history"]] == [
            rain["revision_digest"],
            rain_hold,
        ]
        assert any(c["verdict_revision_digest"] == requirement_hold for c in view["confirmations"])
        older = await rpc(
            "trace/history",
            project_id="radar",
            subject_kind="REQUIREMENT",
            subject_id=REQ,
            before_digest=requirement["revision_digest"],
        )
        assert [h["revision_digest"] for h in older["revisions"]] == [requirement_hold]
        assert older["history_total"] == 2 and older["next_before_digest"] is None
        assert view["set_digest"] == second_set.set_digest
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_a_long_history_comes_in_pages_and_the_stored_trace_does_not_grow_with_it(
    tmp_path: Path,
) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        first, second = demo_set(1), demo_set(2)
        await rpc.load(await rpc.project("radar"), export_csv(first))
        old = {row_key(row) for row in export_rows(first)}
        keep = [row for row in export_rows(second) if row_key(row) not in old]
        for row in keep:
            row["base_set_digest"] = first.set_digest
        await rpc.load("radar", write_csv(keep), "UPDATE")
        sizes: list[int] = []
        for number in range(5):  # five more changes of the rain result: seven revisions in all
            header, rows = table((await rpc("trace/export", project_id="radar"))["csv_text"])
            kind, revision, amount, ident = (
                header.index("row_type"),
                header.index("result_revision"),
                header.index("value"),
                header.index("result_id"),
            )
            for row in rows:
                if row[kind] == "RESULT" and row[ident] == "SYN-RES-SYN-C-DET-RAIN":
                    row[revision], row[amount] = str(int(row[revision]) + 1), f"0.{60 + number}"
            await rpc.load("radar", rebuild(header, rows), "UPDATE")
            sizes.append(await stored_trace_bytes(runtime, "radar"))
        # The trace record lists digests only: each change adds a few dozen bytes, not a revision.
        assert max(b - a for a, b in pairwise(sizes)) < 400

        view = await rpc("trace/read", project_id="radar")
        rain = next(v for v in view["verdicts"] if v["subject_id"] == "SYN-C-DET-RAIN")
        assert rain["history_total"] == 7 and len(rain["recent_history"]) == 3
        assert rain["recent_history"][0]["revision_digest"] == rain["revision_digest"]
        ask = {"project_id": "radar", "subject_kind": "CRITERION", "subject_id": "SYN-C-DET-RAIN"}
        cursor = rain["recent_history"][-1]["revision_digest"]
        page = await rpc("trace/history", before_digest=cursor, limit=2, **ask)
        assert len(page["revisions"]) == 2 and page["history_total"] == 7
        assert page["next_before_digest"] == page["revisions"][-1]["revision_digest"]
        rest = await rpc("trace/history", before_digest=page["next_before_digest"], **ask)
        seen = [
            r["revision_digest"]
            for r in rain["recent_history"] + page["revisions"] + rest["revisions"]
        ]
        assert len(seen) == 7 == len(set(seen)) and rest["next_before_digest"] is None
        # Every older revision still carries what changed and the verdict before it.
        assert rest["revisions"][-1]["parent_digest"] is None
        assert "TRACE_HISTORY_CURSOR_UNKNOWN" in await rpc.error(
            "trace/history", before_digest="f" * 64, **ask
        )
        assert await rpc.error("trace/history", limit=51, **ask)
        everything = await rpc("trace/history", limit=50, **ask)
        assert len(everything["revisions"]) == 7
    finally:
        runtime.close()


@pytest.mark.asyncio
async def test_the_synthetic_notice_survives_import_verdict_and_export(tmp_path: Path) -> None:
    runtime, rpc = await opened(tmp_path)
    try:
        await rpc.load(await rpc.project("a"), export_csv(with_notice(demo_set(1))))
        first = await rpc("trace/export", project_id="a")
        header, rows = table(first["csv_text"])
        assert header[-1] == "synthetic_notice" and {row[-1] for row in rows} == {NOTICE}
        assert all(
            item["fields"]["synthetic_notice"] == NOTICE
            for item in (await rpc("trace/read", project_id="a"))["items"]
        )
        # The rain results arrive in a file made by an older exporter: no notice column at all.
        older = export_csv(demo_set(2))
        older_header, older_rows = table(older)
        digest = first["set_digest"]
        at = {name: number for number, name in enumerate(older_header)}
        update = rebuild(
            older_header,
            [
                [
                    digest if number == at["base_set_digest"] else cell
                    for number, cell in enumerate(row)
                ]
                for row in older_rows
                if row[at["row_type"]] != "ITEM" or row[at["item_id"]].startswith("SYN-RES-")
            ],
        )
        await rpc.load("a", update, "UPDATE")
        after = await rpc("trace/read", project_id="a")
        assert {i["kind"] for i in after["items"] if "synthetic_notice" in i["fields"]} >= {
            "REQUIREMENT"
        }
        assert all(
            i["fields"].get("synthetic_notice") == NOTICE
            for i in after["items"]
            if i["item_id"] == REQ
        )
        again = await rpc("trace/export", project_id="a")
        assert table(again["csv_text"])[0][-1] == "synthetic_notice"
        preview = await rpc(
            "trace/importPreview", project_id="a", mode="UPDATE", csv_text=again["csv_text"]
        )
        assert preview["applicable"] and changes(preview) == (0, 0, 0)
    finally:
        runtime.close()
