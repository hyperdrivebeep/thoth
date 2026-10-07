"""The source list does not read every document's nodes to show each source's time.

A source's time assessment is kept in the version's structure metadata, so the list reads only
that. Counting statements (not seconds) keeps this from depending on the machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from sqlalchemy import event
from tests.integration.storage_coverage_helpers import prepare_project, request, value

from thoth.adapters.storage.artifacts import SqliteArtifactLedger


@pytest.mark.asyncio
async def test_listing_sources_reads_no_document_nodes_and_matches_the_full_read(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    for n in range(3):
        (inbox / f"evidence{n}.md").write_text(
            "# Result\n\n" + "\n\n".join(f"Paragraph {n}-{k} about a clock." for k in range(40)),
            encoding="utf-8",
        )
    runtime, project = await prepare_project(tmp_path)
    try:
        for n in range(3):
            value(
                await runtime.bus.dispatch(
                    request(
                        "project/source/connect",
                        f"sl-connect-{n}",
                        {
                            "project_id": project,
                            "relative_path": f"evidence{n}.md",
                            "media_type": "text/markdown",
                            "authority": "OFFICIAL",
                            "cutoff_state": "ELIGIBLE",
                            "security_class": "INTERNAL",
                        },
                    )
                )
            )
        statements: list[str] = []

        def record(conn: object, cursor: object, statement: str, *rest: object) -> None:
            statements.append(statement)

        engine = runtime.ledger.engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            listed = value(
                await runtime.bus.query(
                    request("project/source/list", "sl-list", {"project_id": project})
                )
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)
        times = cast(list[dict[str, JsonValue]], listed["source_times"])
        assert len(times) == 3
        assert not [s for s in statements if "structural_nodes" in s]
        raw = SqliteArtifactLedger(engine)
        for item in times:
            full = raw.read_source_time(
                project, str(item["artifact_id"]), str(item["source_version_id"])
            )
            assert full is not None
            expected = full.model_dump(mode="json")
            assert {k: item[k] for k in expected} == expected
    finally:
        runtime.close()
