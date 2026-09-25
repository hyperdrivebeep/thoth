import hashlib
import json
from pathlib import Path

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup


@pytest.mark.asyncio
async def test_export_of_research_heads_binds_the_original_source_digest(tmp_path: Path) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel())
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": "p",
                        "problem": "Summarize the connected record.",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        heads = dict(runtime.ledger.read_heads("p"))
        plan = value(
            await runtime.bus.dispatch(
                request(
                    "export/plan/create",
                    "plan",
                    {
                        "project_id": "p",
                        "purpose": "AUDIT_BUNDLE",
                        "recipient": "local reviewer",
                        "trust_boundary": "LOCAL_ONLY",
                        "scope_refs": list(heads),
                        "head_set": heads,
                        "cutoff": "2026-09-13T00:00:00Z",
                        "selection_rules": {},
                        "classification_ceiling": "INTERNAL",
                        "rights_policy_ref": "rights:local",
                        "privacy_policy_ref": "privacy:no-pii",
                        "renderers": ["JSON"],
                    },
                )
            )
        )["export_plan"]
        snapshot = value(
            await runtime.bus.dispatch(
                request(
                    "export/snapshot/create",
                    "snapshot",
                    {
                        "project_id": "p",
                        "export_plan_id": plan["record_id"],
                        "expected_plan_revision": plan["version"],
                    },
                )
            )
        )["export_snapshot"]
        generated = value(
            await runtime.bus.dispatch(
                request(
                    "export/generate",
                    "generate",
                    {"project_id": "p", "export_snapshot_id": snapshot["record_id"]},
                )
            )
        )["export"]
        canonical = json.loads(
            (Path(generated["payload"]["root"]) / "canonical.json").read_text(encoding="utf-8")
        )
        assert canonical["resources"], (
            "A valid envelope with zero source resources is not a grounded export."
        )
        expected_digest = hashlib.sha256(
            (tmp_path / "inbox/records.html").read_bytes()
        ).hexdigest()
        assert [resource["digest"] for resource in canonical["resources"]] == [expected_digest]
        assert any(ref.startswith("THREAD:request:") for ref in canonical["head_set"])
        verified = value(
            await runtime.bus.dispatch(
                request(
                    "export/verify",
                    "verify",
                    {"project_id": "p", "export_id": generated["record_id"]},
                )
            )
        )["verification"]
        assert verified["state"] == "VERIFIED"
    finally:
        runtime.close()
