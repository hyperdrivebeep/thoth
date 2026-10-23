"""Staging bytes alone does not publish a source or make evidence available to research."""

import json
from io import BytesIO
from pathlib import Path

from fastapi import Request, UploadFile
from pydantic import BaseModel
from tests.atomicity.harness import snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.http.file_staging import stage_file_upload
from thoth.domain.model import ModelRequest, ModelResult
from thoth.ports.model import ModelPort


class NoModelCalls:
    calls = 0

    def resolve(self, *, provider: str, model: str | None) -> ModelPort:
        return self

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        self.calls += 1
        raise AssertionError("staged-only evidence must not reach a model")


async def test_staged_bytes_remain_outside_sources_evidence_and_research(tmp_path: Path) -> None:
    models = NoModelCalls()
    workspace = tmp_path / "workspace"
    runtime = create_runtime(workspace, model_resolver=models)
    project, thread = "project:staged-only", "thread:staged-only"
    upload = UploadFile(filename="staged.md", file=BytesIO(b"# Unconnected synthetic evidence\n"))
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": project,
                        "name": "Staged only",
                        "cutoff_at": "2026-10-01T00:00:00Z",
                    },
                )
            )
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "thread",
                    {
                        "project_id": project,
                        "thread_id": thread,
                        "problem": "Read connected evidence",
                        "scope": {},
                    },
                )
            )
        )
        before = snapshot(runtime.ledger.engine)
        staged = await stage_file_upload(
            None,
            Request({"type": "http", "headers": [(b"x-thoth-project-id", project.encode())]}),
            upload,
            _workspace_path_for_request=lambda _request: workspace,
        )
        assert snapshot(runtime.ledger.engine) == before
        path = workspace / "inbox" / str(staged["relative_path"])
        assert path.read_bytes() == b"# Unconnected synthetic evidence\n"
        sources = value(
            await runtime.bus.dispatch(
                request("project/source/list", "sources", {"project_id": project})
            )
        )
        evidence = value(
            await runtime.bus.dispatch(
                request("evidence/list", "evidence", {"project_id": project})
            )
        )
        assert sources["artifacts"] == [] and sources["bindings"] == []
        assert evidence["spans"] == []
        research = await runtime.bus.dispatch(
            request("thread/input", "research", {"project_id": project, "thread_id": thread})
        )
        assert research.error is not None
        assert "requires at least one connected evidence span" in research.error.message
        assert models.calls == 0
        out = Path(__file__).resolve().parents[2] / "outputs/u-upload-20261023"
        out.mkdir(parents=True, exist_ok=True)
        (out / "staged-unconnected-proof.json").write_text(
            json.dumps(
                {
                    "staged_file_exists": True,
                    "database_unchanged_by_staging": True,
                    "sources": sources,
                    "evidence": evidence,
                    "research_error": research.error.model_dump(mode="json"),
                    "model_calls": models.calls,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        await upload.close()
        runtime.close()
