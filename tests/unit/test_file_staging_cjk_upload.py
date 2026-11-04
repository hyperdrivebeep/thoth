"""An uploaded GB18030 text file with a Chinese name is staged, connected and read as Chinese."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.http.app import create_app

PROJECT = "project:cjk-upload"
TEXT = "探测率是指在给定条件下正确发现目标的比例。\n本报告记录了二零二六年的试验结果。\n"


async def test_chinese_named_gb18030_upload_becomes_readable_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_WORKSPACE", str(workspace))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        response = await client.post(
            "/files/stage",
            files={"file": ("雷达报告.txt", TEXT.encode("gb18030"), "text/plain")},
            headers={"x-thoth-project-id": PROJECT},
        )
    assert response.status_code == 200
    staged = response.json()
    assert staged["relative_path"].endswith("-雷达报告.txt")
    assert (workspace / "inbox" / staged["relative_path"]).read_bytes() == TEXT.encode("gb18030")

    runtime = create_runtime(workspace)
    try:
        created: dict[str, object] = {
            "project_id": PROJECT,
            "name": "CJK",
            "cutoff_at": "2026-09-01T00:00:00Z",
        }
        value(await runtime.bus.dispatch(request("project/create", "project", created)))
        connect: dict[str, object] = {
            "project_id": PROJECT,
            "relative_path": staged["relative_path"],
            "media_type": staged["media_type"],
            "authority": "OFFICIAL",
            "cutoff_state": "ELIGIBLE",
        }
        connected = value(
            await runtime.bus.dispatch(request("project/source/connect", "c", connect))
        )
        assert connected["evidence_count"] == 2
        listing = value(
            await runtime.bus.query(request("evidence/list", "e", {"project_id": PROJECT}))
        )
        stored = " ".join(str(item["exact_text"]) for item in listing["spans"])
        assert "探测率是指在给定条件下正确发现目标的比例。" in stored and "\ufffd" not in stored
    finally:
        runtime.close()
