import inspect
from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_a03_storage_atomicity import link_input, setup

from thoth.application.commands.evidence import EvidenceCommandHandlers


@pytest.mark.parametrize("change", ["source", "predecessor"])
async def test_prepared_evidence_rechecks_current_lineage_at_commit(
    tmp_path: Path, change: str
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        registry = runtime.bus._registry  # pyright: ignore[reportPrivateUsage]
        handler = registry.resolve("evidence/link/propose")
        assert inspect.ismethod(handler) and isinstance(handler.__self__, EvidenceCommandHandlers)
        commands = handler.__self__
        service = commands._service  # pyright: ignore[reportPrivateUsage]
        args: dict[str, Any] = {
            **link_input(ctx),
            "thread_id": None,
            "conditions": {},
            "applicability": "WITHIN_STATED_CONDITIONS",
        }
        if change == "predecessor":
            first = value(
                await runtime.bus.dispatch(
                    request("evidence/link/propose", "first", link_input(ctx))
                )
            )["evidence"]
            args["supersedes_evidence_id"] = first["evidence_id"]
        staged = service.stage_link(**args)
        if change == "source":
            value(
                await runtime.bus.dispatch(
                    request(
                        "evidence/source/metadata/correct",
                        "change",
                        {
                            "project_id": ctx["project"],
                            "source_id": ctx["source"],
                            "rights": "UPDATED_RIGHTS",
                        },
                    )
                )
            )
        else:
            value(
                await runtime.bus.dispatch(
                    request(
                        "evidence/revalidate",
                        "change",
                        {
                            "project_id": ctx["project"],
                            "evidence_id": args["supersedes_evidence_id"],
                        },
                    )
                )
            )
        before = snapshot(runtime.ledger.engine)
        with pytest.raises(ValueError, match=r"EVIDENCE_.*REVISION_CONFLICT"):
            service._unit_of_work.commit(staged)  # pyright: ignore[reportPrivateUsage]
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


async def test_explicit_revalidation_binds_current_metadata_and_preserves_old_link(
    tmp_path: Path,
) -> None:
    runtime, ctx = await setup(tmp_path)
    try:
        first = value(
            await runtime.bus.dispatch(request("evidence/link/propose", "first", link_input(ctx)))
        )["evidence"]
        refreshed = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/source/metadata/correct",
                    "change",
                    {
                        "project_id": ctx["project"],
                        "source_id": ctx["source"],
                        "rights": "UPDATED_RIGHTS",
                    },
                )
            )
        )
        response = await runtime.bus.dispatch(
            request(
                "evidence/revalidate",
                "revalidate",
                {"project_id": ctx["project"], "evidence_id": first["evidence_id"]},
            )
        )
        updated = value(response)["evidence"]
        assert updated["source_ids"] == [refreshed["source"]["source_id"]] * len(first["span_ids"])
        assert updated["evidence_id"] != first["evidence_id"]
        assert updated["supersedes_evidence_id"] == first["evidence_id"]
        assert (
            updated["target_id"] == first["target_id"] and updated["span_ids"] == first["span_ids"]
        )
        historical = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/read",
                    "old",
                    {"project_id": ctx["project"], "evidence_id": first["evidence_id"]},
                )
            )
        )
        assert historical["evidence"] == first
    finally:
        runtime.close()
