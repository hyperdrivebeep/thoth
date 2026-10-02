"""The timeline tells what changed in a revision, from the stored parent and child."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request
from tests.integration.test_research_history_read import (
    _rpc_model,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.test_restore_preview_contract import prepared, revise

from thoth.domain.research_history import HistoryPage


@pytest.mark.asyncio
async def test_a_revised_hypothesis_shows_its_statement_change_and_the_first_version_shows_none(
    tmp_path: Path,
) -> None:
    runtime, _model, _accepted, candidates = await prepared(tmp_path)
    try:
        revision, snapshot = candidates["hypothesis.v1"]
        changed = revise(runtime, revision, snapshot, "statement", "지연이 20 ms 늘어난다")
        page = _rpc_model(
            await runtime.bus.query(
                request(
                    "revision/timeline/read",
                    "summary",
                    {"project_id": "p", "scope": {"project_id": "p"}, "limit": 50},
                )
            ),
            HistoryPage,
        )
        by_digest = {item.record_ref.revision_digest: item for item in page.items}
        summary = by_digest[changed.revision_digest].change_summary
        assert summary is not None
        (line,) = summary.lines
        assert line.label == "가설 문장" and line.after == "지연이 20 ms 늘어난다"
        assert summary.flags == ("NUMBER",) and summary.text_diff_recommended
        first = by_digest[revision.revision_digest]
        assert first.change_summary is None
        # requests and results are not diffed as records
        assert all(
            item.change_summary is None for item in page.items if item.kind in {"REQUEST", "RESULT"}
        )
    finally:
        runtime.close()
