from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime

from thoth.domain.canonical import head_set_digest
from thoth.protocol.jsonrpc import JsonRpcRequest, JsonRpcResponse


def request(method: str, key: str, value: dict[str, object]) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(
        {
            "id": key,
            "method": method,
            "params": {"_meta": {"idempotencyKey": key}, "input": value},
        }
    )


def value(response: JsonRpcResponse) -> dict[str, JsonValue]:
    assert response.error is None
    assert response.result is not None
    child = response.result["value"]
    assert isinstance(child, dict)
    return cast(dict[str, JsonValue], child)


@pytest.mark.asyncio
async def test_receipt_closure_and_local_export_without_external_release(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "public.md").write_text(
        "# Public evidence\n\nRights-cleared public evidence.\n",
        encoding="utf-8",
    )
    runtime = create_runtime(workspace)
    project_id = "project:lifecycle-full"
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "lifecycle-project",
                    {
                        "project_id": project_id,
                        "name": "Lifecycle full",
                        "cutoff_at": "2026-08-31T00:00:00Z",
                    },
                )
            )
        )
        connected = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "lifecycle-source",
                    {
                        "project_id": project_id,
                        "relative_path": "public.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "PUBLIC",
                    },
                )
            )
        )
        source = cast(dict[str, JsonValue], connected["source"])
        corrected_source = value(
            await runtime.bus.dispatch(
                request(
                    "evidence/source/metadata/correct",
                    "lifecycle-source-rights",
                    {
                        "project_id": project_id,
                        "source_id": source["source_id"],
                        "rights": "REUSE_ALLOWED",
                        "official_copy_basis": "published rights-cleared fixture",
                    },
                )
            )
        )
        assert cast(dict[str, JsonValue], corrected_source["source"])["rights"] == "REUSE_ALLOWED"
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "lifecycle-thread",
                    {
                        "project_id": project_id,
                        "thread_id": "thread:lifecycle-full",
                        "problem": "Prepare an auditable closure and export",
                        "scope": {"workstream": "integration"},
                    },
                )
            )
        )
        object_id = str(cast(list[str], thread["current_object_ids"])[0])
        heads = dict(runtime.ledger.read_heads(project_id))
        head_digest = head_set_digest(heads)

        sealed_result = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/seal",
                    "receipt-seal",
                    {
                        "project_id": project_id,
                        "receipt_type": "TRANSITION",
                        "claim_scopes": [
                            "TRANSITION_RECORDED",
                            "ARTIFACT_INTEGRITY",
                            "PROVENANCE_BOUND",
                        ],
                        "subject_refs": list(heads.values()),
                        "before_head_set_digest": head_digest,
                        "after_head_set_digest": head_digest,
                        "actor_or_agent_ref": "agent:lifecycle-test",
                        "evidence_refs": [],
                        "policy_version": "receipt:1",
                        "model_versions": [],
                        "tool_versions": ["thoth:test"],
                    },
                )
            )
        )
        sealed = cast(dict[str, JsonValue], sealed_result["receipt"])
        receipt_id = str(sealed["receipt_id"])
        assert sealed_result["semantic_truth_certified"] is False
        assert sealed["actor_id"] == "agent:lifecycle-test"
        assert sealed["session_id"] is None
        assert sealed["role_assignment_ref"] is None
        assert sealed["provenance_state"] == "PARTIAL"
        verified_result = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/verify",
                    "receipt-verify",
                    {"project_id": project_id, "receipt_id": receipt_id},
                )
            )
        )
        verification = cast(dict[str, JsonValue], verified_result["verification"])
        verification_payload = cast(dict[str, JsonValue], verification["payload"])
        assert verification["state"] == "VERIFIED"
        assert verification_payload["timestamp_trust"] == "LOCAL_ONLY"
        assert verification_payload["semantic_truth_certified"] is False

        receipt_ids = [
            str(item["receipt_id"])
            for item in cast(
                list[dict[str, JsonValue]],
                value(
                    await runtime.bus.dispatch(
                        request(
                            "receipt/list",
                            "receipt-list",
                            {"project_id": project_id},
                        )
                    )
                )["receipts"],
            )
        ]
        bundle_result = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/bundle/create",
                    "receipt-bundle",
                    {
                        "project_id": project_id,
                        "receipt_ids": receipt_ids,
                    },
                )
            )
        )
        bundle = cast(dict[str, JsonValue], bundle_result["bundle"])
        bundle_id = str(bundle["record_id"])
        bundle_verify = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/bundle/verify",
                    "receipt-bundle-verify",
                    {"project_id": project_id, "bundle_id": bundle_id},
                )
            )
        )
        assert bundle_verify["integrity_state"] == "VALID"
        corrected_receipt_result = value(
            await runtime.bus.dispatch(
                request(
                    "receipt/correction/create",
                    "receipt-correction",
                    {
                        "project_id": project_id,
                        "receipt_id": receipt_id,
                        "correction_reason": "clarify subject scope",
                        "corrected_subject_refs": [heads[f"DECISION_OBJECT:{object_id}"]],
                    },
                )
            )
        )
        corrected_receipt = cast(dict[str, JsonValue], corrected_receipt_result["receipt"])
        assert corrected_receipt_result["original_mutated"] is False
        assert corrected_receipt["parent_receipt_refs"] == [receipt_id]

        closure_package_result = value(
            await runtime.bus.dispatch(
                request(
                    "closure/prepare",
                    "closure-prepare",
                    {
                        "project_id": project_id,
                        "resolution": "Scope work completed without success equivalence",
                        "unresolved_refs": [],
                        "open_effect_refs": [],
                    },
                )
            )
        )
        closure_package = cast(dict[str, JsonValue], closure_package_result["closure"])
        ledger_closure_id = str(closure_package["closure_id"])
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "closure/package/read",
                        "closure-package-read",
                        {
                            "project_id": project_id,
                            "closure_id": ledger_closure_id,
                        },
                    )
                )
            )["closure_package"],
            dict,
        )
        readiness_result = value(
            await runtime.bus.dispatch(
                request(
                    "closure/readiness/assess",
                    "closure-readiness",
                    {
                        "project_id": project_id,
                        "closure_scope": "DECISION_OBJECT",
                        "scope_ref": object_id,
                        "disposition": "DEFERRED",
                        "open_items": [
                            {
                                "item_id": "monitor-evidence",
                                "disposition": "DEFERRED",
                                "residual_risk": {
                                    "description": "Evidence may change the draft",
                                    "state": "UNKNOWN",
                                    "basis_refs": [],
                                },
                                "description": "monitor residual evidence",
                                "owner_ref": "human:project-owner",
                                "trigger_or_due": "next review window",
                            }
                        ],
                        "required_receipt_refs": [receipt_id],
                    },
                )
            )
        )
        readiness = cast(dict[str, JsonValue], readiness_result["readiness"])
        readiness_id = str(readiness["record_id"])
        assert readiness["state"] == "READY_WITH_OPEN_ITEMS"
        closure_decision_result = value(
            await runtime.bus.dispatch(
                request(
                    "closure/decide",
                    "closure-decide",
                    {
                        "project_id": project_id,
                        "readiness_id": readiness_id,
                        "decision": "DECIDE",
                        "actor_ref": "human:project-owner",
                        "reason": "open item has owner and trigger",
                    },
                )
            )
        )
        closure_decision = cast(dict[str, JsonValue], closure_decision_result["closure"])
        closure_id = str(closure_decision["record_id"])
        followup = value(
            await runtime.bus.dispatch(
                request(
                    "closure/followup/create",
                    "closure-followup",
                    {
                        "project_id": project_id,
                        "closure_id": closure_id,
                        "purpose": "monitor residual evidence",
                        "owner_ref": "human:project-owner",
                        "trigger_or_due": "next review window",
                    },
                )
            )
        )
        assert isinstance(followup["followup"], dict)
        reopen = value(
            await runtime.bus.dispatch(
                request(
                    "closure/reopen",
                    "closure-reopen",
                    {
                        "project_id": project_id,
                        "closure_id": closure_id,
                        "reason": "new evidence changed the decision need",
                        "child_scope_ref": "object:child-followup",
                    },
                )
            )
        )
        assert cast(dict[str, JsonValue], reopen["reopen"])["state"] == "CHILD_CREATED"
        retention = value(
            await runtime.bus.dispatch(
                request(
                    "closure/retention/plan",
                    "closure-retention",
                    {
                        "project_id": project_id,
                        "closure_id": closure_id,
                        "retention_policy_ref": "retention:project-v1",
                        "legal_or_security_hold": True,
                    },
                )
            )
        )
        assert cast(dict[str, JsonValue], retention["retention"])["state"] == "ON_HOLD"
        purge = value(
            await runtime.bus.dispatch(
                request(
                    "closure/purge/prepare",
                    "closure-purge",
                    {
                        "project_id": project_id,
                        "closure_id": closure_id,
                        "exact_scope_refs": ["artifact:test"],
                        "reason": "test protected purge preparation",
                    },
                )
            )
        )
        assert purge["deletion_performed"] is False
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "closure/list",
                        "closure-list",
                        {"project_id": project_id},
                    )
                )
            )["closures"],
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "closure/openItem/list",
                        "closure-open-items",
                        {"project_id": project_id, "closure_id": readiness_id},
                    )
                )
            )["open_items"],
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "closure/reopen/read",
                        "closure-reopen-read",
                        {"project_id": project_id, "closure_id": closure_id},
                    )
                )
            )["reopens"],
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "closure/retention/read",
                        "closure-retention-read",
                        {"project_id": project_id, "closure_id": closure_id},
                    )
                )
            )["retention"],
        )

        current_heads = dict(runtime.ledger.read_heads(project_id))
        export_plan_result = value(
            await runtime.bus.dispatch(
                request(
                    "export/plan/create",
                    "export-plan",
                    {
                        "project_id": project_id,
                        "purpose": "AUDIT_BUNDLE",
                        "recipient": "external reviewer",
                        "trust_boundary": "EXTERNAL_PROTECTED",
                        "scope_refs": list(current_heads),
                        "cutoff": "2026-08-31T00:00:00Z",
                        "head_set": current_heads,
                        "selection_rules": {"include": "rights-cleared public"},
                        "classification_ceiling": "PUBLIC",
                        "rights_policy_ref": "rights:reuse-allowed",
                        "privacy_policy_ref": "privacy:no-pii",
                        "renderers": ["JSON"],
                    },
                )
            )
        )
        export_plan = cast(dict[str, JsonValue], export_plan_result["export_plan"])
        export_plan_id = str(export_plan["record_id"])
        export_snapshot_result = value(
            await runtime.bus.dispatch(
                request(
                    "export/snapshot/create",
                    "export-snapshot",
                    {
                        "project_id": project_id,
                        "export_plan_id": export_plan_id,
                        "expected_plan_revision": export_plan["version"],
                    },
                )
            )
        )
        export_snapshot = cast(dict[str, JsonValue], export_snapshot_result["export_snapshot"])
        export_snapshot_id = str(export_snapshot["record_id"])
        generated_result = value(
            await runtime.bus.dispatch(
                request(
                    "export/generate",
                    "export-generate",
                    {
                        "project_id": project_id,
                        "export_snapshot_id": export_snapshot_id,
                    },
                )
            )
        )
        generated = cast(dict[str, JsonValue], generated_result["export"])
        export_id = str(generated["record_id"])
        generated_payload = cast(dict[str, JsonValue], generated["payload"])
        root = Path(str(generated_payload["root"]))
        assert (root / "canonical.json").is_file()
        assert (root / "manifest.json").is_file()
        assert (root / "ro-crate-metadata.json").is_file()
        assert generated_payload["external_transmission_performed"] is False
        export_verify_result = value(
            await runtime.bus.dispatch(
                request(
                    "export/verify",
                    "export-verify",
                    {"project_id": project_id, "export_id": export_id},
                )
            )
        )
        export_verification = cast(dict[str, JsonValue], export_verify_result["verification"])
        export_verification_payload = cast(dict[str, JsonValue], export_verification["payload"])
        assert export_verification["state"] == "VERIFIED"
        assert export_verification_payload["release_eligible"] is True
        release_result = value(
            await runtime.bus.dispatch(
                request(
                    "export/release/prepare",
                    "export-release",
                    {
                        "project_id": project_id,
                        "export_id": export_id,
                        "target": "external reviewer",
                        "release_boundary": "EXTERNAL_PROTECTED",
                        "actor_ref": "human:project-owner",
                    },
                )
            )
        )
        release = cast(dict[str, JsonValue], release_result["release"])
        release_payload = cast(dict[str, JsonValue], release["payload"])
        assert release_payload["risk_tier"] == "R3"
        assert release_payload["transmitted"] is False
        corrected_export_result = value(
            await runtime.bus.dispatch(
                request(
                    "export/correction/create",
                    "export-correction",
                    {
                        "project_id": project_id,
                        "export_id": export_id,
                        "correction_reason": "clarify manifest note",
                        "corrections": {"manifest_note": "clarified"},
                    },
                )
            )
        )
        assert corrected_export_result["original_overwritten"] is False
        corrected_export = cast(dict[str, JsonValue], corrected_export_result["corrected_export"])

        for method, key, record_id, field in (
            ("export/read", "export-read", export_id, "export"),
            ("export/plan/read", "export-plan-read", export_plan_id, "plan"),
            (
                "export/snapshot/read",
                "export-snapshot-read",
                export_snapshot_id,
                "snapshot",
            ),
            ("export/manifest/read", "export-manifest-read", export_id, "manifest"),
            ("export/artifact/list", "export-artifact-list", export_id, "artifacts"),
        ):
            result = value(
                await runtime.bus.dispatch(
                    request(
                        method,
                        key,
                        {"project_id": project_id, "export_id": record_id},
                    )
                )
            )
            assert result[field]
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "export/verification/read",
                        "export-verification-read",
                        {"project_id": project_id, "export_id": export_id},
                    )
                )
            )["verifications"],
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "export/release/read",
                        "export-release-read",
                        {"project_id": project_id, "export_id": export_id},
                    )
                )
            )["releases"],
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "export/correction/read",
                        "export-correction-read",
                        {
                            "project_id": project_id,
                            "export_id": corrected_export["record_id"],
                        },
                    )
                )
            )["corrections"],
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "export/list",
                        "export-list",
                        {"project_id": project_id},
                    )
                )
            )["exports"],
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "receipt/audit/read",
                        "receipt-audit",
                        {"project_id": project_id},
                    )
                )
            )["receipts"],
        )
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "receipt/bundle/read",
                        "receipt-bundle-read",
                        {"project_id": project_id, "bundle_id": bundle_id},
                    )
                )
            )["bundle"],
            dict,
        )
        assert cast(
            list[object],
            value(
                await runtime.bus.dispatch(
                    request(
                        "receipt/verification/read",
                        "receipt-verification-read",
                        {"project_id": project_id, "receipt_id": receipt_id},
                    )
                )
            )["verifications"],
        )
        assert isinstance(
            value(
                await runtime.bus.dispatch(
                    request(
                        "receipt/lineage/read",
                        "receipt-lineage",
                        {
                            "project_id": project_id,
                            "receipt_id": corrected_receipt["receipt_id"],
                        },
                    )
                )
            )["parent_receipt_dag"],
            list,
        )
    finally:
        runtime.close()
