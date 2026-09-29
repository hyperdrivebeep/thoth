"""Opt-in author time assertion for an undated synthetic local source."""

from typing import cast

from pydantic import JsonValue
from tests.integration.storage_coverage_helpers import request, value

from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.apps.runtime import AppRuntime


async def confirm_synthetic_source_time(
    runtime: AppRuntime,
    project_id: str,
    connected: dict[str, JsonValue],
    *,
    key: str,
) -> dict[str, JsonValue]:
    artifact = cast(dict[str, JsonValue], connected["artifact"])
    original_source = cast(dict[str, JsonValue], connected["source"])
    artifact_id = str(artifact["artifact_id"])
    version_id = str(connected["source_version_id"])
    listed = value(
        await runtime.bus.query(
            request("project/source/list", key + ":basis", {"project_id": project_id})
        )
    )
    assessment = next(
        item
        for item in cast(list[dict[str, JsonValue]], listed["source_times"])
        if item["artifact_id"] == artifact_id and item["source_version_id"] == version_id
    )
    assert assessment["cutoff_state"] == "UNKNOWN_TIME"
    cutoff = cast(dict[str, JsonValue], listed["cutoff_basis"])
    confirmed = value(
        await runtime.bus.dispatch(
            request(
                "project/source/time/confirm",
                key + ":confirm",
                {
                    "project_id": project_id,
                    "artifact_id": artifact_id,
                    "source_version_id": version_id,
                    "byte_sha256": assessment["byte_sha256"],
                    "expected_project_revision": cutoff["project_revision"],
                    "expected_cutoff_at": cutoff["cutoff_at"],
                    "expected_assessment_revision": assessment["revision"],
                    "expected_metadata_digest": assessment["metadata_digest"],
                    "assertion": "ON_OR_BEFORE_CUTOFF",
                },
            )
        )
    )
    assert cast(dict[str, JsonValue], confirmed["source_time"])["cutoff_state"] == "ELIGIBLE"
    current = SqliteEvidenceGraphStore(runtime.ledger.engine).read_source_by_artifact(artifact_id)
    assert current is not None and current.cutoff_eligibility.value == "ELIGIBLE"
    assert current.source_id != original_source["source_id"]
    evidence = value(
        await runtime.bus.query(
            request("evidence/list", key + ":readback", {"project_id": project_id})
        )
    )
    spans = [
        span
        for span in cast(list[dict[str, JsonValue]], evidence["spans"])
        if span["artifact_id"] == artifact_id
    ]
    assert spans and all(span["cutoff_state"] == "ELIGIBLE" for span in spans)
    return cast(dict[str, JsonValue], current.model_dump(mode="json"))
