"""A change-set commit moves the heads, rebuilds the baseline and records the commit as one unit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.atomicity.fault_helpers import assert_only_failed_command_bookkeeping
from tests.atomicity.harness import snapshot
from tests.integration.scoped_runtime import create_runtime
from tests.integration.storage_coverage_helpers import request, value

from thoth.application.services.baseline_service import BaselineService
from thoth.application.services.control_record_service import ControlRecordService
from thoth.apps.runtime import AppRuntime
from thoth.domain.control_record import ControlRecord

PROJECT = "project:revision-commit"


async def call(runtime: AppRuntime, method: str, key: str, body: dict[str, object]):
    return value(await runtime.bus.dispatch(request(method, key, body)))


async def ready_change_set(workspace: Path):
    runtime = create_runtime(workspace)
    created: dict[str, object] = {
        "project_id": PROJECT,
        "name": "Revision",
        "cutoff_at": "2026-08-31T00:00:00Z",
    }
    await call(runtime, "project/create", "project", created)
    thread: dict[str, object] = {
        "project_id": PROJECT,
        "thread_id": "thread:revision-commit",
        "problem": "Revise safely",
        "scope": {"workstream": "integration"},
    }
    started = await call(runtime, "thread/start", "thread", thread)
    object_id = str(started["current_object_ids"][0])
    heads = await call(runtime, "revision/head/read", "heads", {"project_id": PROJECT})
    working: dict[str, str] = heads["working_heads"]
    key = "DECISION_OBJECT:" + object_id
    old = working[key]
    revision = await call(
        runtime, "revision/read", "rev", {"project_id": PROJECT, "revision_digest": old}
    )
    content = await call(
        runtime,
        "revision/content/read",
        "content",
        {"project_id": PROJECT, "content_digest": revision["content_digest"]},
    )
    candidate = {**content["snapshot"]["content"], "purpose_statement": "Revised purpose"}
    proposal_body: dict[str, object] = {
        "project_id": PROJECT,
        "aggregate_id": object_id,
        "aggregate_type": "DECISION_OBJECT",
        "parent_revision_digests": [old],
        "candidate_content": candidate,
        "reason": "evidence test",
        "evidence_refs": [],
        "actor_or_agent_ref": "agent:evidence-test",
        "expected_head_digest": old,
    }
    proposal = (await call(runtime, "revision/propose", "proposal", proposal_body))["proposal"]
    create_body: dict[str, object] = {
        "project_id": PROJECT,
        "expected_head_set": working,
        "candidate_revision_digests": [proposal["record_digest"]],
        "transition_reason": "evidence test",
        "impact_policy_ref": "impact:default",
    }
    change_set = (await call(runtime, "revision/changeSet/create", "change-set", create_body))[
        "change_set"
    ]
    validate: dict[str, object] = {
        "project_id": PROJECT,
        "record_id": change_set["record_id"],
        "expected_change_set_revision": change_set["version"],
    }
    validated = await call(runtime, "revision/changeSet/validate", "validate", validate)
    assert validated["state"] == "READY_TO_COMMIT"
    commit: dict[str, object] = {
        "project_id": PROJECT,
        "record_id": change_set["record_id"],
        "validation_bundle_digest": validated["validation_bundle_digest"],
        "expected_head_set_digest": heads["project_head_set_digest"],
    }
    return runtime, key, old, str(change_set["record_id"]), commit


def head_of(runtime: AppRuntime, key: str) -> str | None:
    return dict(runtime.ledger.read_heads(PROJECT)).get(key)


@pytest.mark.parametrize("point", ["baseline_refresh", "change_set_record"])
async def test_change_set_commit_fault_after_the_heads_move_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    runtime, key, old, _record_id, commit = await ready_change_set(tmp_path / "workspace")
    try:
        command = request("revision/changeSet/commit", "commit-fault", commit)
        before = snapshot(runtime.ledger.engine)
        reached = {"n": 0}
        real = ControlRecordService.create
        with monkeypatch.context() as patch:
            if point == "baseline_refresh":

                def refresh_fails(self: BaselineService, **kwargs: object) -> None:
                    reached["n"] += 1
                    raise RuntimeError("injected fault during the baseline refresh")

                patch.setattr(BaselineService, "refresh", refresh_fails)
            else:

                def record_fails(self: ControlRecordService, **kwargs: Any) -> ControlRecord:
                    if kwargs.get("record_type") == "CHANGE_SET" and kwargs.get("state") in {
                        "COMMITTED",
                        "BRANCHED",
                    }:
                        reached["n"] += 1
                        raise RuntimeError("injected fault writing the committed CHANGE_SET")
                    return real(self, **kwargs)

                patch.setattr(ControlRecordService, "create", record_fails)
            result = await runtime.bus.dispatch(command)
        assert result.error is not None and reached["n"] == 1
        assert_only_failed_command_bookkeeping(runtime.ledger.engine, before, command)
        assert head_of(runtime, key) == old, "the head did not move"
        retried = await call(runtime, "revision/changeSet/commit", "commit-retry", commit)
        assert retried["atomic_commit"] is True
        assert head_of(runtime, key) != old
    finally:
        runtime.close()
