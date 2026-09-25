"""Public source setup for legacy RPC/fault scenarios that now require a real contract."""

import json
from pathlib import Path
from typing import cast

from tests.integration.storage_coverage_helpers import request, value

from thoth.apps.runtime import AppRuntime


async def connect_local_measurement_contract(
    runtime: AppRuntime,
    project_id: str,
    workspace: Path,
    *,
    confirm_source_time: bool = True,
) -> str:
    document = {
        "schema_version": "1.0.0",
        "record_type": "RESEARCH_MEASUREMENT_CONTRACT",
        "contract_id": "measurement:latency-mean",
        "method": "ARITHMETIC_MEAN",
        "procedure_version": "numeric-mean:1",
        "measure": "latency",
        "unit": "ms",
        "conditions": {"dataset_version": "trial-v1"},
        "minimum_samples": 2,
        "maximum_samples": 100,
        "image_digest": "scripted:a04",
        "runtime_version": "scripted:1",
    }
    inbox = workspace / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "measurement-contract.json").write_text(
        json.dumps(document, sort_keys=True), encoding="utf-8"
    )
    connected = value(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                "measurement-contract",
                {
                    "project_id": project_id,
                    "relative_path": "measurement-contract.json",
                    "media_type": "application/json",
                    "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE",
                    "security_class": "INTERNAL",
                },
            )
        )
    )
    artifact_id = cast(str, connected["artifact"]["artifact_id"])
    source_version_id = cast(str, connected["source_version_id"])
    listed_before = value(
        await runtime.bus.query(
            request("project/source/list", "measurement-time-before", {"project_id": project_id})
        )
    )
    evidence_before = value(
        await runtime.bus.query(
            request("evidence/list", "measurement-spans-before", {"project_id": project_id})
        )
    )
    artifacts_before = {item["artifact_id"]: item for item in listed_before["artifacts"]}
    times_before = {item["artifact_id"]: item for item in listed_before["source_times"]}
    spans_before = {item["span_id"]: item for item in evidence_before["spans"]}
    artifact = artifacts_before[artifact_id]
    assessment = times_before[artifact_id]
    contract_spans = [
        item for item in spans_before.values() if item["artifact_id"] == artifact_id
    ]
    assert artifact["cutoff_state"] == assessment["cutoff_state"] == "UNKNOWN_TIME"
    assert assessment["reason_code"] == "NO_DOCUMENT_DATE"
    assert assessment["source_version_id"] == source_version_id
    assert contract_spans and all(
        item["cutoff_state"] == "UNKNOWN_TIME" for item in contract_spans
    )
    assert all(item["authority_state"] == "INFORMAL" for item in contract_spans)
    assert any(
        "RESEARCH_MEASUREMENT_CONTRACT" in item["exact_text"] for item in contract_spans
    )
    if not confirm_source_time:
        return artifact_id

    cutoff = listed_before["cutoff_basis"]
    confirmed = value(
        await runtime.bus.dispatch(
            request(
                "project/source/time/confirm",
                "measurement-time-confirm",
                {
                    "project_id": project_id,
                    "artifact_id": artifact_id,
                    "source_version_id": source_version_id,
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
    assert confirmed["source_time"]["cutoff_state"] == "ELIGIBLE"
    listed_after = value(
        await runtime.bus.query(
            request("project/source/list", "measurement-time-after", {"project_id": project_id})
        )
    )
    evidence_after = value(
        await runtime.bus.query(
            request("evidence/list", "measurement-spans-after", {"project_id": project_id})
        )
    )
    artifacts_after = {item["artifact_id"]: item for item in listed_after["artifacts"]}
    times_after = {item["artifact_id"]: item for item in listed_after["source_times"]}
    spans_after = {item["span_id"]: item for item in evidence_after["spans"]}
    assert artifacts_after.keys() == artifacts_before.keys()
    assert times_after.keys() == times_before.keys()
    assert spans_after.keys() == spans_before.keys()
    assert listed_after["bindings"] == listed_before["bindings"]
    for other_id in artifacts_before.keys() - {artifact_id}:
        assert artifacts_after[other_id] == artifacts_before[other_id]
        assert times_after[other_id] == times_before[other_id]
    for key in ("artifact_id", "source_uri", "media_type", "byte_sha256", "authority"):
        assert artifacts_after[artifact_id][key] == artifact[key]
    after_time = times_after[artifact_id]
    assert artifacts_after[artifact_id]["cutoff_state"] == after_time["cutoff_state"] == "ELIGIBLE"
    assert after_time["mode"] == "USER_CONFIRMATION"
    assert after_time["reason_code"] == "USER_CONFIRMED_ON_OR_BEFORE"
    assert after_time["revision"] == assessment["revision"] + 1
    for key in ("artifact_id", "source_version_id", "byte_sha256"):
        assert after_time[key] == assessment[key]
    for span_id, before_span in spans_before.items():
        after_span = spans_after[span_id]
        if before_span["artifact_id"] != artifact_id:
            assert after_span == before_span
            continue
        assert after_span["cutoff_state"] == "ELIGIBLE"
        for key in (
            "span_id",
            "artifact_id",
            "source_version_id",
            "locator",
            "text_sha256",
            "exact_text",
            "authority_state",
        ):
            assert after_span[key] == before_span[key]
    return artifact_id
