"""project/source/connect is all-or-nothing in the database; only designed leftovers remain.

By design, byte staging precedes the database write and connector IO and cleanup observations are
recorded separately. After a failed connect exactly these remain: two connector IO records, two
connector cleanup-usage records and one staged object blob. Nothing else may be added.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from tests.atomicity.harness import (
    AllowedRow,
    Snapshot,
    assert_phase_delta,
    failed_command_allowances,
    snapshot,
)
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

import thoth.adapters.storage.acquisition as acquisition_module

DOCUMENT = (
    "# Title\n\nPara one about wind.\n\nPara two about rain.\n\nPara three about fog.\n\n"
    "- item a\n- item b\n"
)
DESIGNED_SURVIVORS = [
    ("CLEANUP_USAGE", "COMPLETED"),
    ("CLEANUP_USAGE", "PENDING"),
    ("IO", "RETURNED"),
    ("IO", "RETURNED"),
]
OBSERVATION_TYPES = ("IO", "CLEANUP_USAGE")


def designed_survivors(
    before: Snapshot, after: Snapshot
) -> tuple[tuple[AllowedRow, ...], list[tuple[str, str]]]:
    """Rows a failed connect may add: only connector IO and cleanup observations."""
    added = Counter(after["control_records"]) - Counter(before["control_records"])
    rows = [json.loads(raw) for raw in added.elements()]
    kept = [
        r for r in rows if r["namespace"] == "CONNECTOR" and r["record_type"] in OBSERVATION_TYPES
    ]
    unexpected = [r for r in rows if r not in kept]
    assert not unexpected, [(r["namespace"], r["record_type"]) for r in unexpected]
    allowed = tuple(
        AllowedRow(
            "control_records", {"control_revision_id": r["control_revision_id"]}, "added", {}
        )
        for r in kept
    )
    return allowed, sorted((r["record_type"], r["state"]) for r in kept)


def blobs(root: Path) -> set[str]:
    base = root / "objects"
    return {p.name for p in base.rglob("*") if p.is_file()} if base.exists() else set()


@pytest.mark.parametrize("fail_at_call", [3, 7, 12])
async def test_connect_fault_inside_the_ingestion_write_is_all_or_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at_call: int
) -> None:
    runtime = create_runtime(tmp_path)
    project = "project:source-fault"
    created: dict[str, object] = {
        "project_id": project,
        "name": "P",
        "cutoff_at": "2026-09-01T00:00:00Z",
    }
    value(await runtime.bus.dispatch(request("project/create", "project", created)))
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "source.md").write_text(DOCUMENT, encoding="utf-8")
    body: dict[str, object] = {
        "project_id": project,
        "relative_path": "source.md",
        "media_type": "text/markdown",
        "authority": "OFFICIAL",
        "cutoff_state": "ELIGIBLE",
    }
    command = request("project/source/connect", "connect-fault", body)
    before = snapshot(runtime.ledger.engine)
    blobs_before = blobs(tmp_path)
    real = vars(acquisition_module)["_dump"]
    calls = {"n": 0}

    def flaky(obj: object) -> str:
        calls["n"] += 1
        if calls["n"] == fail_at_call:
            raise RuntimeError("injected write fault inside ingestion")
        return real(obj)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(acquisition_module, "_dump", flaky)
            result = await runtime.bus.dispatch(command)
        assert result.error is not None, "the injected fault must surface"
        assert calls["n"] >= fail_at_call, "the fault point was reached"
        after = snapshot(runtime.ledger.engine)
        extra, kept = designed_survivors(before, after)
        allowed = failed_command_allowances(runtime.ledger.engine, command) + extra
        assert_phase_delta(before, after, allowed)
        assert kept == DESIGNED_SURVIVORS
        assert len(blobs(tmp_path) - blobs_before) == 1, "exactly the staged bytes remain"
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        assert_phase_delta(before, snapshot(reopened.ledger.engine), allowed)
        listing = request("project/source/list", "listed", {"project_id": project})
        sources = value(await reopened.bus.query(listing))
        assert sources["artifacts"] == [] and sources["bindings"] == []
        retry = request("project/source/connect", "connect-retry", body)
        assert value(await reopened.bus.dispatch(retry))["evidence_count"] > 0
        listing = request("project/source/list", "listed-again", {"project_id": project})
        listed = value(await reopened.bus.query(listing))
        assert len(listed["artifacts"]) == 1 and len(listed["bindings"]) == 1
    finally:
        reopened.close()
