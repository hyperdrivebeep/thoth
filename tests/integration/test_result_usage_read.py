"""A thread read reports usage and elapsed time for the result's own operation only."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup


@pytest.mark.asyncio
async def test_thread_read_reports_the_current_results_own_usage(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=True)
    try:
        started = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "first",
                    {
                        "project_id": "p",
                        "problem": "LAB-42 지연 조건을 요약해줘",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        first_op = str(started["operation_id"])
        read = value(
            await runtime.bus.query(
                request("thread/read", "r1", {"project_id": "p", "thread_id": started["thread_id"]})
            )
        )
        first = cast(dict[str, dict[str, object]], read["result_usage"])
        assert first_op in first
        assert cast(int, first[first_op]["calls"]) > 0
        # the controlled model reports no tokens: unknown stays unknown, not zero
        assert first[first_op]["total_tokens"] is None and first[first_op]["state"] == "UNKNOWN"
        wall_ms = first[first_op]["wall_ms"]
        assert isinstance(wall_ms, int) and wall_ms >= 0

        second = value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "second",
                    {
                        "project_id": "p",
                        "thread_id": started["thread_id"],
                        "instruction": "두 번째 조건",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        second_op = str(second["operation_id"])
        assert second_op != first_op
        read = value(
            await runtime.bus.query(
                request("thread/read", "r2", {"project_id": "p", "thread_id": started["thread_id"]})
            )
        )
        usage = cast(dict[str, dict[str, object]], read["result_usage"])
        assert second_op in usage and first_op not in usage
        # the thread-wide total still covers both operations; the per-result entry only its own
        thread_calls = cast(dict[str, int], read["usage"])["model_transports"]
        assert cast(int, usage[second_op]["calls"]) < thread_calls
    finally:
        runtime.close()
