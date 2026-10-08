"""A denied result cannot be read or consume a visible page slot; cursor order stays stable."""

from datetime import UTC, datetime
from pathlib import Path
from types import MethodType
from typing import cast

from pytest import MonkeyPatch
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.storage.threads import SqliteThreadStore
from thoth.application.commands.research_followup import ResearchFollowupHandlers
from thoth.domain.research_followup import ProjectReviewListInput
from thoth.domain.revision import SemanticRevision
from thoth.protocol.registry import MethodRegistry


async def test_review_pages_filter_before_read_and_preserve_tied_thread_order(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        for suffix in ("a", "b", "c"):
            thread_id = f"thread:review-order-{suffix}"
            accepted = value(
                await runtime.bus.dispatch(
                    request(
                        "thread/start",
                        f"review-order-{suffix}",
                        {
                            "project_id": "p",
                            "thread_id": thread_id,
                            "problem": "Review missing evidence",
                            "contract_version": 2,
                        },
                    )
                )
            )
            assert accepted["thread_id"] == thread_id
            await runtime.bus.drain()
        registry = cast(MethodRegistry, vars(runtime.bus)["_registry"])
        handlers = cast(
            ResearchFollowupHandlers,
            cast(MethodType, registry.resolve("project/review/list")).__self__,
        )
        reviews = handlers.reviews
        threads = SqliteThreadStore(runtime.ledger.engine)
        for suffix in ("a", "b", "c"):
            thread = threads.read(f"thread:review-order-{suffix}")
            assert thread is not None
            assert threads.update(
                thread.model_copy(update={"updated_at": datetime(2026, 10, 8, tzinfo=UTC)}),
                expected_revision=thread.revision,
            )
        denied = runtime.ledger.read_heads("p")["DECISION_OBJECT:result:thread:review-order-b"]
        original_access = reviews.access.may_read_revision
        original_read = reviews.ledger.read_revision_by_digest
        reads: list[str] = []

        def may_read(project_id: str, digest: str) -> bool:
            return digest != denied and original_access(project_id, digest)

        def read(project_id: str, digest: str) -> SemanticRevision | None:
            reads.append(digest)
            return original_read(project_id, digest)

        monkeypatch.setattr(reviews.access, "may_read_revision", may_read)
        monkeypatch.setattr(reviews.ledger, "read_revision_by_digest", read)
        before = snapshot(runtime.ledger.engine)
        first = reviews.list(ProjectReviewListInput(project_id="p", limit=1))
        second = reviews.list(
            ProjectReviewListInput(project_id="p", limit=1, cursor=first.next_cursor)
        )
        assert [item.thread_id for item in first.items] == ["thread:review-order-c"]
        assert first.next_cursor == "1" and first.coverage == "CONTINUATION"
        assert [item.thread_id for item in second.items] == ["thread:review-order-a"]
        assert second.next_cursor is None and second.coverage == "COMPLETE_PAGE"
        assert denied not in reads
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()
