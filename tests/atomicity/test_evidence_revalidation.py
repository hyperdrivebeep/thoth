import inspect
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.evidence_helpers import evidence_service
from tests.atomicity.harness import assert_phase_delta, failed_command_allowances, snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import link_input, setup

from thoth.adapters.runtime import SystemClock
from thoth.adapters.storage.artifacts import SqliteArtifactLedger
from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.domain.enums import CutoffState


@pytest.mark.parametrize("point", ["add_link", "append_audit"])
async def test_metadata_change_during_revalidation_rolls_back_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        first = value(
            await runtime.bus.dispatch(request("evidence/link/propose", "first", link_input(ctx)))
        )["evidence"]
        service = evidence_service(runtime)
        original = getattr(SqliteEvidenceGraphStore, point)
        changed = False

        def interfere(store: SqliteEvidenceGraphStore, record: Any) -> None:
            nonlocal changed
            original(store, record)
            if not changed:
                changed = True
                service.correct_source_metadata(
                    project_id=ctx["project"],
                    source_id=ctx["source"],
                    rights="changed during publication",
                    retention=None,
                    official_copy_basis=None,
                )

        before = snapshot(runtime.ledger.engine)
        command = request(
            "evidence/revalidate",
            "interference",
            {"project_id": ctx["project"], "evidence_id": first["evidence_id"]},
        )
        with monkeypatch.context() as patch:
            patch.setattr(SqliteEvidenceGraphStore, point, interfere)
            response = await runtime.bus.dispatch(command)
        assert changed and response.error is not None
        assert_phase_delta(
            before,
            snapshot(runtime.ledger.engine),
            failed_command_allowances(runtime.ledger.engine, command),
        )
    finally:
        runtime.close()


async def test_historical_artifact_is_not_replaced_and_disconnected_source_leaves_current_feed(
    tmp_path: Path,
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        first = value(
            await runtime.bus.dispatch(request("evidence/link/propose", "first", link_input(ctx)))
        )["evidence"]
        (tmp_path / "inbox/new.md").write_text("A different document version.", encoding="utf-8")
        newer = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "new",
                    {
                        "project_id": ctx["project"],
                        "relative_path": "new.md",
                        "media_type": "text/markdown",
                    },
                )
            )
        )
        refresh = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/source/refresh",
                    "refresh",
                    {
                        "project_id": ctx["project"],
                        "artifact_id": newer["artifact"]["artifact_id"],
                        "source_id": ctx["source"],
                    },
                )
            )
        )
        revalidate = request(
            "evidence/revalidate",
            "historical",
            {"project_id": ctx["project"], "evidence_id": first["evidence_id"]},
        )
        updated = value(await runtime.bus.dispatch(revalidate))["evidence"]
        assert (
            updated["span_ids"] == first["span_ids"]
            and updated["source_ids"] == first["source_ids"]
        )
        assert refresh["source"]["source_id"] not in updated["source_ids"]
        sources = value(
            await runtime.bus.query(
                request("project/source/list", "sources", {"project_id": ctx["project"]})
            )
        )
        old_binding = next(
            b for b in sources["bindings"] if b["artifact_id"] != newer["artifact"]["artifact_id"]
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/disconnect",
                    "revoke",
                    {
                        "project_id": ctx["project"],
                        "binding_id": old_binding["binding_id"],
                        "mode": "REVOKE",
                    },
                )
            )
        )
        handler = runtime.bus._registry.resolve("thread/input")  # pyright: ignore[reportPrivateUsage]
        assert inspect.ismethod(handler) and isinstance(handler.__self__, ResearchThreadHandlers)
        assert all(
            span.artifact_id != old_binding["artifact_id"]
            for span in handler.__self__.analysis.evidence(ctx["project"])
        )
    finally:
        runtime.close()


async def test_multi_artifact_tied_timestamp_selection_replay_and_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        (tmp_path / "inbox/second.md").write_text(
            "An informal second measurement.", encoding="utf-8"
        )
        second = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "second",
                    {
                        "project_id": ctx["project"],
                        "relative_path": "second.md",
                        "media_type": "text/markdown",
                        "authority": "UNCLASSIFIED",
                        "cutoff_state": "ELIGIBLE",
                    },
                )
            )
        )
        spans = value(
            await runtime.bus.query(
                request("evidence/list", "spans", {"project_id": ctx["project"]})
            )
        )["spans"]
        second_span = next(
            span["span_id"]
            for span in spans
            if span["artifact_id"] == second["artifact"]["artifact_id"]
        )
        payload: dict[str, object] = {**link_input(ctx), "span_ids": [ctx["spans"][0], second_span]}
        first = value(
            await runtime.bus.dispatch(request("evidence/link/propose", "first", payload))
        )["evidence"]
        instant = SystemClock().now()

        def same_time(clock: SystemClock) -> datetime:
            return instant

        monkeypatch.setattr(SystemClock, "now", same_time)
        current_source = ctx["source"]
        for index in range(2):
            corrected = value(
                await runtime.bus.dispatch(
                    request(
                        "evidence/source/metadata/correct",
                        f"correct-{index}",
                        {
                            "project_id": ctx["project"],
                            "source_id": current_source,
                            "rights": f"revision {index}",
                        },
                    )
                )
            )
            current_source = corrected["source"]["source_id"]
        command = request(
            "evidence/revalidate",
            "revalidate",
            {"project_id": ctx["project"], "evidence_id": first["evidence_id"]},
        )
        updated = value(await runtime.bus.dispatch(command))["evidence"]
        assert updated["source_ids"] == [current_source, second["source"]["source_id"]]
        assert (
            updated["support_status"] == "UNRESOLVED"
            and updated["authority_status"] == "UNCLASSIFIED"
        )
        before_replay = snapshot(runtime.ledger.engine)
        assert value(await runtime.bus.dispatch(command))["evidence"] == updated
        assert_phase_delta(before_replay, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()
    reopened = create_runtime(tmp_path)
    try:
        read = value(
            await reopened.bus.dispatch(
                request(
                    "evidence/read",
                    "read",
                    {"project_id": ctx["project"], "evidence_id": updated["evidence_id"]},
                )
            )
        )
        assert read["evidence"] == updated
    finally:
        reopened.close()


async def test_span_basis_change_rejects_stage_and_limits_explicit_revalidation(
    tmp_path: Path,
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        service = evidence_service(runtime)
        args: dict[str, Any] = {
            **link_input(ctx),
            "thread_id": None,
            "conditions": {},
            "applicability": "WITHIN_STATED_CONDITIONS",
        }
        staged = service.stage_link(**args)
        raw = SqliteArtifactLedger(runtime.ledger.engine)
        span = raw.read_evidence(ctx["spans"][0])
        assert span is not None
        raw.update_evidence(
            span.model_copy(update={"cutoff_state": CutoffState.PROHIBITED_CONTEXT})
        )
        before = snapshot(runtime.ledger.engine)
        with pytest.raises(ValueError, match="EVIDENCE_SPAN_REVISION_CONFLICT"):
            service._unit_of_work.commit(staged)  # pyright: ignore[reportPrivateUsage]
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
        first = value(
            await runtime.bus.dispatch(request("evidence/link/propose", "new", link_input(ctx)))
        )["evidence"]
        updated = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/revalidate",
                    "revalidate",
                    {"project_id": ctx["project"], "evidence_id": first["evidence_id"]},
                )
            )
        )["evidence"]
        assert updated["support_status"] == "UNRESOLVED"
    finally:
        runtime.close()
