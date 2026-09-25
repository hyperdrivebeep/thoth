from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from tests.integration.storage_coverage_helpers import (
    domain_snapshot,
    prepare_project,
    request,
    value,
)

from thoth.adapters.export_bundle import FilesystemExportBundle
from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.control_record import SqliteControlRecordStore
from thoth.adapters.storage.evidence_graph import SqliteEvidenceGraphStore
from thoth.application.commands import receipts_full
from thoth.application.services.control_record_service import ControlRecordService
from thoth.apps.runtime import AppRuntime, create_runtime
from thoth.domain.auth import AuthenticatedActorContext
from thoth.domain.canonical import head_set_digest
from thoth.domain.control_record import ControlRecord
from thoth.domain.export_snapshot import FrozenExportSnapshot, StagedExportBundle
from thoth.protocol.jsonrpc import RpcErrorCode


async def rpc(
    runtime: AppRuntime, method: str, key: str, project: str, **kwargs: Any
) -> dict[str, Any]:
    return value(
        await runtime.bus.dispatch(request(method, key, {"project_id": project, **kwargs}))
    )


async def source(
    runtime: AppRuntime, project: str, workspace: Path, name: str, security: str = "PUBLIC"
):
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(f"# {name}\n\nA bounded public observation.\n", encoding="utf-8")
    connected = await rpc(
        runtime,
        "project/source/connect",
        "source-" + name,
        project,
        relative_path=name,
        media_type="text/markdown",
        authority="OFFICIAL",
        cutoff_state="ELIGIBLE",
        security_class=security,
    )
    return connected["source"]


async def plan(
    runtime: AppRuntime, project: str, scopes: list[str], key: str = "plan", **overrides: Any
):
    fields = {
        "purpose": "AUDIT_BUNDLE",
        "recipient": "local reviewer",
        "trust_boundary": "LOCAL_ONLY",
        "scope_refs": scopes,
        "cutoff": "2026-09-05T00:00:00Z",
        "head_set": dict(runtime.ledger.read_heads(project)),
        "selection_rules": {},
        "classification_ceiling": "PUBLIC",
        "rights_policy_ref": "rights:local",
        "privacy_policy_ref": "privacy:local",
        "renderers": ["JSON"],
        **overrides,
    }
    return (await rpc(runtime, "export/plan/create", key, project, **fields))["export_plan"]


async def seal_snapshot(
    runtime: AppRuntime, project: str, planned: dict[str, Any], key: str = "snapshot"
):
    return (
        await rpc(
            runtime,
            "export/snapshot/create",
            key,
            project,
            export_plan_id=planned["record_id"],
            expected_plan_revision=planned["version"],
        )
    )["export_snapshot"]


async def test_public_receipt_claims_stay_partial_and_evidence_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, project = await prepare_project(tmp_path)
    actor = AuthenticatedActorContext(
        actor_id="actor:assertion",
        session_id="session:assertion",
        project_id=project,
        role_assignment_id="role:assertion",
        role="PROJECT_OWNER",
        capabilities=("*",),
        data_scopes=("*",),
    )
    monkeypatch.setattr(receipts_full, "current_authenticated_actor", lambda: actor)
    try:
        evidence = await source(runtime, project, tmp_path, "receipt.md")
        sealed = (
            await rpc(
                runtime,
                "receipt/seal",
                "public-assertion",
                project,
                receipt_type="TRANSITION",
                claim_scopes=["PROVENANCE_BOUND"],
                subject_refs=[evidence["artifact_id"]],
                before_head_set_digest="a" * 64,
                after_head_set_digest="b" * 64,
                actor_or_agent_ref=actor.actor_id,
                evidence_refs=[evidence["source_id"]],
                policy_version="asserted-policy",
            )
        )["receipt"]
        assert sealed["provenance_state"] == "PARTIAL"
        assert sealed["semantic_truth_certified"] is False
        assert sealed["evidence_refs"] == [evidence["source_id"]]
        receipt_id = sealed["receipt_id"]
    finally:
        runtime.close()
    runtime = create_runtime(tmp_path)
    try:
        stored = (await rpc(runtime, "receipt/read", "read-back", project, receipt_id=receipt_id))[
            "receipt"
        ]
        assert stored["evidence_refs"] == [evidence["source_id"]]
        assert stored["receipt_digest"] == sealed["receipt_digest"]
    finally:
        runtime.close()


async def test_export_scope_is_frozen_and_later_artifact_is_not_added(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        first = await source(runtime, project, tmp_path, "first.md")
        planned = await plan(runtime, project, [first["artifact_id"]])
        snapshot = await seal_snapshot(runtime, project, planned)
        await source(runtime, project, tmp_path, "unrelated.md")
        generated = (
            await rpc(
                runtime,
                "export/generate",
                "generate",
                project,
                export_snapshot_id=snapshot["record_id"],
            )
        )["export"]
        data = json.loads((Path(generated["payload"]["root"]) / "canonical.json").read_text())
        assert [item["artifact_id"] for item in data["resources"]] == [first["artifact_id"]]
    finally:
        runtime.close()


async def test_unknown_scope_rejected_without_whole_project_fallback(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        await source(runtime, project, tmp_path, "first.md")
        before = domain_snapshot(runtime.ledger.engine)
        result = await runtime.bus.dispatch(
            request(
                "export/plan/create",
                "unknown-plan",
                {
                    "project_id": project,
                    "purpose": "AUDIT_BUNDLE",
                    "recipient": "local reviewer",
                    "trust_boundary": "LOCAL_ONLY",
                    "scope_refs": ["unresolved:scope"],
                    "cutoff": "2026-09-05T00:00:00Z",
                    "head_set": dict(runtime.ledger.read_heads(project)),
                    "selection_rules": {},
                    "classification_ceiling": "PUBLIC",
                    "rights_policy_ref": "rights:local",
                    "privacy_policy_ref": "privacy:local",
                    "renderers": ["JSON"],
                },
            )
        )
        assert result.error is not None
        assert result.error.data == {"reason_code": "RESOURCE_REFERENCE_UNRESOLVED"}
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_local_public_ceiling_excludes_internal_resources(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        restricted = await source(runtime, project, tmp_path, "internal.md", "INTERNAL")
        snapshot = await seal_snapshot(
            runtime, project, await plan(runtime, project, [restricted["artifact_id"]])
        )
        generated = (
            await rpc(
                runtime,
                "export/generate",
                "generate",
                project,
                export_snapshot_id=snapshot["record_id"],
            )
        )["export"]
        data = json.loads((Path(generated["payload"]["root"]) / "canonical.json").read_text())
        assert data["resources"] == []
        assert generated["payload"]["release_eligible"] is False
    finally:
        runtime.close()


async def test_release_cannot_widen_local_boundary_or_recipient(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        first = await source(runtime, project, tmp_path, "first.md")
        snapshot = await seal_snapshot(
            runtime, project, await plan(runtime, project, [first["artifact_id"]])
        )
        generated = (
            await rpc(
                runtime,
                "export/generate",
                "generate",
                project,
                export_snapshot_id=snapshot["record_id"],
            )
        )["export"]
        await rpc(runtime, "export/verify", "verify", project, export_id=generated["record_id"])
        for index, (boundary, target) in enumerate(
            (
                ("EXTERNAL_PROTECTED", "local reviewer"),
                ("LOCAL_ONLY", "another recipient"),
            )
        ):
            result = await runtime.bus.dispatch(
                request(
                    "export/release/prepare",
                    f"release-{index}",
                    {
                        "project_id": project,
                        "export_id": generated["record_id"],
                        "target": target,
                        "release_boundary": boundary,
                        "actor_ref": "human:reviewer",
                    },
                )
            )
            assert result.error is not None
        release = await rpc(
            runtime,
            "export/release/prepare",
            "release-ok",
            project,
            export_id=generated["record_id"],
            target="local reviewer",
            release_boundary="LOCAL_ONLY",
            actor_ref="human:reviewer",
        )
        assert release["release"]["payload"]["transmitted"] is False
    finally:
        runtime.close()


@pytest.mark.parametrize("command", ["receipt", "closure", "export"])
async def test_group_fault_rolls_back_domain_records_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        if command == "receipt":
            first = await source(runtime, project, tmp_path, "receipt-fault.md")
            sealed = (
                await rpc(
                    runtime,
                    "receipt/seal",
                    "receipt",
                    project,
                    receipt_type="TRANSITION",
                    claim_scopes=["TRANSITION_RECORDED"],
                    subject_refs=[first["artifact_id"]],
                    before_head_set_digest=head_set_digest({}),
                    after_head_set_digest=head_set_digest({}),
                    actor_or_agent_ref="local",
                    evidence_refs=[],
                    policy_version="local",
                )
            )["receipt"]
            method, fields, fault_type = (
                "receipt/correction/create",
                {
                    "receipt_id": sealed["receipt_id"],
                    "correction_reason": "annotation",
                    "corrected_subject_refs": [first["source_id"]],
                },
                "CORRECTION",
            )
        elif command == "closure":
            method, fields, fault_type = (
                "closure/readiness/assess",
                {
                    "closure_scope": "PROJECT_PHASE",
                    "scope_ref": project,
                    "disposition": "DEFERRED",
                    "open_items": [
                        {
                            "description": "Follow up",
                            "owner_ref": "human",
                            "trigger_or_due": "later",
                        }
                    ],
                },
                "OPEN_ITEM",
            )
        else:
            first = await source(runtime, project, tmp_path, "first.md")
            snapshot = await seal_snapshot(
                runtime, project, await plan(runtime, project, [first["artifact_id"]])
            )
            generated = (
                await rpc(
                    runtime,
                    "export/generate",
                    "generate",
                    project,
                    export_snapshot_id=snapshot["record_id"],
                )
            )["export"]
            method, fields, fault_type = (
                "export/correction/create",
                {
                    "export_id": generated["record_id"],
                    "correction_reason": "annotation",
                    "corrections": {"manifest_note": "clarified"},
                },
                "GENERATED_EXPORT",
            )
        before = domain_snapshot(runtime.ledger.engine)
        original = SqliteControlRecordStore.append
        fault_reached = False

        def fail(store: SqliteControlRecordStore, record: ControlRecord) -> None:
            nonlocal fault_reached
            original(store, record)
            if record.record_type == fault_type:
                fault_reached = True
                raise RuntimeError("injected A10 post-write failure")

        monkeypatch.setattr(SqliteControlRecordStore, "append", fail)
        result = await runtime.bus.dispatch(
            request(method, "fault", {"project_id": project, **fields})
        )
        assert fault_reached
        assert result.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()
    runtime = create_runtime(tmp_path)
    try:
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_failed_repeat_generation_does_not_corrupt_previous_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        first = await source(runtime, project, tmp_path, "first.md")
        snapshot = await seal_snapshot(
            runtime, project, await plan(runtime, project, [first["artifact_id"]])
        )
        generated = (
            await rpc(
                runtime,
                "export/generate",
                "generate",
                project,
                export_snapshot_id=snapshot["record_id"],
            )
        )["export"]
        root = Path(generated["payload"]["root"])
        files = {path.name: path.read_bytes() for path in root.iterdir()}
        await source(runtime, project, tmp_path, "unrelated.md")
        before = domain_snapshot(runtime.ledger.engine)
        original = ControlRecordService.create

        def fail(service: ControlRecordService, **kwargs: Any):
            result = original(service, **kwargs)
            if kwargs.get("record_type") == "GENERATED_EXPORT":
                raise RuntimeError("injected generated export failure")
            return result

        monkeypatch.setattr(ControlRecordService, "create", fail)
        response = await runtime.bus.dispatch(
            request(
                "export/generate",
                "repeat",
                {
                    "project_id": project,
                    "export_snapshot_id": snapshot["record_id"],
                },
            )
        )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
        assert {path.name: path.read_bytes() for path in root.iterdir()} == files
    finally:
        runtime.close()


async def test_plan_revision_cas_and_frozen_rights_drift(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        first = await source(runtime, project, tmp_path, "first.md")
        planned = await plan(runtime, project, [first["artifact_id"]])
        stale = await runtime.bus.dispatch(
            request(
                "export/snapshot/create",
                "stale-version",
                {
                    "project_id": project,
                    "export_plan_id": planned["record_id"],
                    "expected_plan_revision": 2,
                },
            )
        )
        assert stale.error is not None
        snapshot = await seal_snapshot(runtime, project, planned)
        await rpc(
            runtime,
            "evidence/source/metadata/correct",
            "rights-change",
            project,
            source_id=first["source_id"],
            rights="PROHIBITED",
            official_copy_basis="rights revoked",
        )
        result = await runtime.bus.dispatch(
            request(
                "export/generate",
                "generate",
                {
                    "project_id": project,
                    "export_snapshot_id": snapshot["record_id"],
                },
            )
        )
        assert result.error is not None
    finally:
        runtime.close()


async def test_headset_aggregate_without_evidence_does_not_expand_all_artifacts(
    tmp_path: Path,
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        await source(runtime, project, tmp_path, "unrelated.md")
        await rpc(
            runtime,
            "thread/start",
            "thread",
            project,
            thread_id="thread:scoped",
            problem="Record a bounded decision",
            scope={"workstream": "test"},
        )
        heads = dict(runtime.ledger.read_heads(project))
        scope = next(
            key
            for key, digest in heads.items()
            if (revision := runtime.ledger.read_revision_by_digest(project, digest)) is not None
            and not revision.evidence_refs
        )
        snapshot = await seal_snapshot(runtime, project, await plan(runtime, project, [scope]))
        generated = (
            await rpc(
                runtime,
                "export/generate",
                "generate",
                project,
                export_snapshot_id=snapshot["record_id"],
            )
        )["export"]
        data = json.loads((Path(generated["payload"]["root"]) / "canonical.json").read_text())
        assert data["head_set"] == {scope: heads[scope]}
        assert data["resources"] == []
    finally:
        runtime.close()


async def test_closure_decision_rejects_stale_readiness(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        readiness = (
            await rpc(
                runtime,
                "closure/readiness/assess",
                "ready",
                project,
                closure_scope="PROJECT_PHASE",
                scope_ref=project,
                disposition="DEFERRED",
            )
        )["readiness"]
        await rpc(
            runtime,
            "thread/start",
            "thread-after-ready",
            project,
            thread_id="thread:later",
            problem="New current project state",
            scope={"workstream": "test"},
        )
        before = domain_snapshot(runtime.ledger.engine)
        response = await runtime.bus.dispatch(
            request(
                "closure/decide",
                "decide",
                {
                    "project_id": project,
                    "readiness_id": readiness["record_id"],
                    "decision": "DECIDE",
                    "actor_ref": "human:reviewer",
                    "reason": "close",
                },
            )
        )
        assert response.error is not None
        assert domain_snapshot(runtime.ledger.engine) == before
    finally:
        runtime.close()


async def test_partial_receipt_is_not_upgraded_by_bundle_or_audit(tmp_path: Path) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        first = await source(runtime, project, tmp_path, "partial.md")
        sealed = (
            await rpc(
                runtime,
                "receipt/seal",
                "partial",
                project,
                receipt_type="TRANSITION",
                claim_scopes=["PROVENANCE_BOUND"],
                subject_refs=[first["artifact_id"]],
                before_head_set_digest="a" * 64,
                after_head_set_digest="b" * 64,
                actor_or_agent_ref="assertion",
                evidence_refs=[first["source_id"]],
                policy_version="assertion",
            )
        )["receipt"]
        assert sealed["provenance_state"] == "PARTIAL"
        await rpc(
            runtime, "receipt/bundle/create", "bundle", project, receipt_ids=[sealed["receipt_id"]]
        )
        audit = await rpc(runtime, "receipt/audit/read", "audit", project)
        nodes = [
            node
            for node in audit["dag"]["nodes"]
            if node["source_receipt_ref"] == sealed["receipt_id"]
        ]
        assert nodes and all(node["axes"]["provenance"] == "PARTIAL" for node in nodes)
        assert audit["dag"]["axes"]["provenance"] == "PARTIAL"
    finally:
        runtime.close()


async def test_selected_prohibited_source_is_not_replaced_by_newer_allowed_source(
    tmp_path: Path,
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        old = await source(runtime, project, tmp_path, "old.md")
        prohibited = (
            await rpc(
                runtime,
                "evidence/source/metadata/correct",
                "prohibit-old",
                project,
                source_id=old["source_id"],
                rights="PROHIBITED",
            )
        )["source"]
        assert prohibited["source_id"] != old["source_id"]
        assert prohibited["rights"] == "PROHIBITED"
        refreshed = await rpc(
            runtime,
            "evidence/source/refresh",
            "allow-new",
            project,
            artifact_id=old["artifact_id"],
            source_id=prohibited["source_id"],
            rights="REUSE_ALLOWED",
        )
        assert refreshed["source"]["source_id"] != prohibited["source_id"]
        selected = SqliteEvidenceGraphStore(runtime.ledger.engine).read_source(
            str(prohibited["source_id"])
        )
        assert selected is not None and selected.rights == "PROHIBITED"
        snapshot = await seal_snapshot(
            runtime, project, await plan(runtime, project, [prohibited["source_id"]])
        )
        generated = (
            await rpc(
                runtime,
                "export/generate",
                "generate",
                project,
                export_snapshot_id=snapshot["record_id"],
            )
        )["export"]
        data = json.loads((Path(generated["payload"]["root"]) / "canonical.json").read_text())
        assert data["resources"] == []
        assert generated["payload"]["release_eligible"] is False
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["unrelated_head", "rights"])
async def test_file_staging_allows_peer_writer_and_rechecks_only_bound_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    runtime, project = await prepare_project(tmp_path)
    peer = create_runtime(tmp_path)
    original = FilesystemExportBundle.stage
    changed = False
    try:
        selected = await source(runtime, project, tmp_path, "stage.md")
        snapshot = await seal_snapshot(
            runtime,
            project,
            await plan(
                runtime,
                project,
                [selected["artifact_id"]],
            ),
        )
        before_heads = dict(runtime.ledger.read_heads(project))

        async def change_peer() -> None:
            nonlocal changed
            if change == "rights":
                corrected = await rpc(
                    peer,
                    "evidence/source/metadata/correct",
                    "during-stage-rights",
                    project,
                    source_id=selected["source_id"],
                    rights="PROHIBITED",
                )
                assert corrected["source"]["rights"] == "PROHIBITED"
            else:
                await rpc(
                    peer,
                    "thread/start",
                    "during-stage-thread",
                    project,
                    thread_id="thread:export-peer",
                    problem="Unrelated new research question",
                )
                assert dict(peer.ledger.read_heads(project)) != before_heads
            changed = True

        def stage(
            self: FilesystemExportBundle,
            frozen: FrozenExportSnapshot,
            digest: str,
        ) -> StagedExportBundle:
            staged = original(self, frozen, digest)
            # A real second runtime must finish its write while file staging is active.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: asyncio.run(change_peer())).result(timeout=15)
            return staged

        monkeypatch.setattr(FilesystemExportBundle, "stage", stage)
        response = await runtime.bus.dispatch(
            request(
                "export/generate",
                "stage-generate",
                {
                    "project_id": project,
                    "export_snapshot_id": snapshot["record_id"],
                },
            )
        )
        assert changed
        generated = SqliteControlRecordStore(runtime.ledger.engine).list(
            project,
            "EXPORT",
            "GENERATED_EXPORT",
        )
        if change == "rights":
            assert response.error is not None
            assert response.error.code == RpcErrorCode.DOMAIN_REJECTED
            assert generated == ()
        else:
            result = value(response)["export"]
            assert len(generated) == 1
            dataset = json.loads((Path(result["payload"]["root"]) / "canonical.json").read_text())
            assert [item["artifact_id"] for item in dataset["resources"]] == [
                selected["artifact_id"]
            ]
            assert dataset["head_set"] == {}
    finally:
        runtime.close()
        peer.close()


async def test_legacy_records_are_preserved_but_unknown_scope_is_not_exposed(
    tmp_path: Path,
) -> None:
    runtime, project = await prepare_project(tmp_path)
    try:
        selected = await source(runtime, project, tmp_path, "legacy.md")
        planned = await plan(runtime, project, [selected["artifact_id"]])
        snapshot = await seal_snapshot(runtime, project, planned)
        generated = (
            await rpc(
                runtime,
                "export/generate",
                "modern-generate",
                project,
                export_snapshot_id=snapshot["record_id"],
            )
        )["export"]
        controls = ControlRecordService(
            store=SqliteControlRecordStore(runtime.ledger.engine),
            clock=SystemClock(),
            ids=UuidIdGenerator(),
        )
        legacy_snapshot = controls.create(
            project_id=project,
            namespace="EXPORT",
            record_type="SNAPSHOT",
            state="SNAPSHOT_SEALED",
            payload={"plan_id": planned["record_id"]},
        )
        # These are the prior stored-envelope fields. Valid file hashes alone cannot
        # reconstruct the missing frozen scope/authority binding.
        payload = {
            key: generated["payload"][key]
            for key in (
                "root",
                "artifacts",
                "manifest",
                "resources",
                "excluded_resources",
            )
        }
        payload.update({"snapshot_id": legacy_snapshot.record_id, "plan_id": planned["record_id"]})
        legacy_export = controls.create(
            project_id=project,
            namespace="EXPORT",
            record_type="GENERATED_EXPORT",
            state="GENERATED",
            payload=payload,
        )
        before = domain_snapshot(runtime.ledger.engine)
        for method, values in (
            ("export/snapshot/read", {"export_id": legacy_snapshot.record_id}),
            ("export/verify", {"export_id": legacy_export.record_id}),
            (
                "export/release/prepare",
                {
                    "export_id": legacy_export.record_id,
                    "target": "local reviewer",
                    "release_boundary": "LOCAL_ONLY",
                    "actor_ref": "human:reviewer",
                },
            ),
        ):
            response = await runtime.bus.dispatch(
                request(method, "legacy-" + method, {"project_id": project, **values})
            )
            assert response.error is not None
            assert response.error.data == {"reason_code": "RESOURCE_SCOPE_UNKNOWN"}
        listed = await rpc(runtime, "export/list", "legacy-list", project)
        assert legacy_snapshot.record_id not in str(listed)
        assert legacy_export.record_id not in str(listed)
        response = await runtime.bus.dispatch(
            request(
                "export/generate",
                "legacy-generate",
                {"project_id": project, "export_snapshot_id": legacy_snapshot.record_id},
            )
        )
        assert response.error is not None
        assert response.error.code == RpcErrorCode.DOMAIN_REJECTED
        assert response.error.message == "EXPORT_LEGACY_SNAPSHOT_RESEAL_REQUIRED"
        assert domain_snapshot(runtime.ledger.engine) == before
        # Unknown legacy bindings stay byte-for-byte available to the storage owner.
        raw = SqliteControlRecordStore(runtime.ledger.engine)
        assert raw.read(project, "EXPORT", legacy_snapshot.record_id) == legacy_snapshot
        assert raw.read(project, "EXPORT", legacy_export.record_id) == legacy_export
    finally:
        runtime.close()
