"""Synthetic cold-restart evidence; no user workspace or real provider is opened."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from thoth.adapters.models.local_credentials import LocalModelCredentials
from thoth.adapters.models.xai_broker import XaiAuthBroker
from thoth.adapters.models.xai_profile import XaiCredential, XaiProfile, XaiProfileHold
from thoth.adapters.storage.objects import ContentAddressedObjectStore
from thoth.adapters.storage.workspace_setup import read_setup, write_setup
from thoth.domain.workspace_setup import WorkspaceSetupState

_PROCESS = r"""
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel
from thoth.adapters.models.catalog import StaticModelCatalog
from thoth.adapters.storage.objects import ContentAddressedObjectStore
from thoth.apps.runtime import create_runtime
from thoth.domain.model_settings import ModelOption, ModelSelection

workspace = Path(sys.argv[1])
mode = sys.argv[2]
thread_id = sys.argv[3] if len(sys.argv) > 3 else None
raw = b"THOTH synthetic restart object: original bytes"
digest = hashlib.sha256(raw).hexdigest()
model = ControlledResearchModel()
catalog = StaticModelCatalog(
    (ModelOption(provider="synthetic", model="fixed", reasoning_efforts=("medium",),
                 default_effort="medium", capability_source="controlled-restart-test"),),
    defaults=ModelSelection(provider="synthetic", model="fixed", reasoning_effort="medium"),
)

async def run():
    global thread_id
    runtime = create_runtime(
        workspace, model_resolver=model, model_catalog=catalog,
        resource_scope_policy=fixture_scope_policy(),
    )
    try:
        if mode == "write":
            inbox = workspace / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            (inbox / "record.html").write_text(
                '<html><head><meta property="article:published_time" '
                'content="2026-09-01T00:00:00Z" /></head><body>'
                '<h1>Latency record</h1><p>LAB-42: 12 ms under condition alpha.</p>'
                '<p>Counterexample: no repeat under beta.</p></body></html>',
                encoding="utf-8",
            )
            value(await runtime.bus.dispatch(request(
                "project/create", "restart-create", {"project_id": "project:restart",
                    "name": "Restart", "cutoff_at": "2026-09-13T00:00:00Z"},
            )))
            value(await runtime.bus.dispatch(request(
                "workspace/setup/update", "restart-consent", {"internet_consent": "DENIED"},
            )))
            value(await runtime.bus.dispatch(request(
                "project/source/connect", "restart-source", {
                    "project_id": "project:restart", "relative_path": "record.html",
                    "media_type": "text/html", "authority": "INFORMAL",
                    "cutoff_state": "ELIGIBLE", "security_class": "INTERNAL"},
            )))
            value(await runtime.bus.dispatch(request(
                "model/settings/update", "restart-model", {
                    "project_id": "project:restart", "selection": {
                        "provider": "synthetic", "model": "fixed", "reasoning_effort": "medium"}},
            )))
            ContentAddressedObjectStore(workspace).put(raw, digest, operation_id="restart-object")
            started = value(await runtime.bus.dispatch(request(
                "thread/start", "restart-thread", {"project_id": "project:restart",
                    "problem": "What does the latency record support?", "contract_version": 2},
            )))
            thread_id = started["thread_id"]
            await runtime.bus.drain()
            operation = runtime.bus.read_operation(str(started["operation_id"]))
            assert operation is not None and operation.state.value == "SUCCEEDED", operation
        assert isinstance(thread_id, str)
        setup = value(await runtime.bus.query(request(
            "workspace/setup/read", "read-setup-" + mode, {},
        )))
        settings = value(await runtime.bus.query(request(
            "model/settings/read", "read-model-" + mode, {"project_id": "project:restart"},
        )))
        thread = value(await runtime.bus.query(request(
            "thread/read", "read-thread-" + mode,
            {"project_id": "project:restart", "thread_id": thread_id},
        )))
        sources = value(await runtime.bus.query(request(
            "project/source/list", "read-sources-" + mode, {"project_id": "project:restart"},
        )))
        evidence = value(await runtime.bus.query(request(
            "evidence/list", "read-evidence-" + mode, {"project_id": "project:restart"},
        )))
        spans = evidence["evidence"]
        assert spans
        first_span = value(await runtime.bus.dispatch(request(
            "evidence/read", "read-span-" + mode,
            {"project_id": "project:restart", "span_id": spans[0]["span_id"]},
        )))
        assert ContentAddressedObjectStore(workspace).read(digest) == raw
        result = thread["current_result"]
        assert result is not None
        def encoded_digest(item):
            return hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()

        return {
            "thread_id": thread_id,
            "result_operation_id": result["operation_id"],
            "request_revision_id": result["request_ref"]["revision_id"],
            "request_revision_digest": result["request_ref"]["revision_digest"],
            "result_basis_digest": result["basis_digest"],
            "setup_revision": setup.get("revision"),
            "internet_consent": setup.get("internet_consent"),
            "setup_status": setup.get("setup_status"),
            "settings_digest": settings["settings_digest"],
            "selection": settings["selection"],
            "result_digest": encoded_digest(result),
            "source_digest": encoded_digest(sources["artifacts"]),
            "evidence_digest": encoded_digest(first_span["evidence"]),
            "object_digest": digest,
            "model_calls": len(model.calls),
        }
    finally:
        runtime.close()

print(json.dumps(asyncio.run(run()), sort_keys=True))
"""


def _child_env(home: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "local"),
        "APPDATA": str(home / "roaming"),
        "XDG_DATA_HOME": str(home / "xdg"),
        "CODEX_HOME": str(home / "codex"),
    }


def _run(workspace: Path, mode: str, thread_id: str | None, home: Path) -> dict[str, object]:
    env = _child_env(home)
    command = [sys.executable, "-B", "-c", _PROCESS, str(workspace), mode]
    if thread_id is not None:
        command.append(thread_id)
    child = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout.strip().splitlines()[-1])


def test_process_reopen_and_cold_copy_preserve_research_and_selection(tmp_path: Path) -> None:
    original = tmp_path / "original"
    home = tmp_path / "synthetic-home"
    home.mkdir()
    written = _run(original, "write", None, home)
    assert isinstance(written["model_calls"], int) and written["model_calls"] > 0
    assert written["internet_consent"] == "DENIED"
    reopened = _run(original, "read", str(written["thread_id"]), home)
    assert reopened["model_calls"] == 0
    assert {key: val for key, val in reopened.items() if key != "model_calls"} == {
        key: val for key, val in written.items() if key != "model_calls"
    }

    copied = tmp_path / "cold-copy"
    shutil.copytree(original, copied)
    copied_read = _run(copied, "read", str(written["thread_id"]), home)
    assert copied_read["model_calls"] == 0
    assert {key: val for key, val in copied_read.items() if key != "model_calls"} == {
        key: val for key, val in written.items() if key != "model_calls"
    }


def test_corrupt_setup_keeps_saved_result_and_evidence_readable(tmp_path: Path) -> None:
    workspace = tmp_path / "original"
    home = tmp_path / "synthetic-home"
    home.mkdir()
    written = _run(workspace, "write", None, home)
    source = workspace / "workspace-setup.json"
    source.write_bytes(b"{damaged")
    reopened = _run(workspace, "read-corrupt", str(written["thread_id"]), home)
    assert reopened["setup_status"] == "CORRUPT"
    assert reopened["internet_consent"] is None
    assert reopened["model_calls"] == 0
    for key in (
        "thread_id", "result_operation_id", "request_revision_id",
        "request_revision_digest", "result_basis_digest", "settings_digest",
        "selection", "result_digest", "source_digest", "evidence_digest", "object_digest",
    ):
        assert reopened[key] == written[key]
    assert source.read_bytes() == b"{damaged"


@pytest.mark.parametrize("interrupted_side", ["generation", "auth"])
def test_xai_pair_interruption_holds_auth_but_preserves_saved_research(
    tmp_path: Path, interrupted_side: str
) -> None:
    original = tmp_path / "original"
    home = tmp_path / "synthetic-home"
    home.mkdir()
    written = _run(original, "write", None, home)
    old = XaiCredential("old-access", "old-refresh", time.time() + 3600, "old-gen")
    profile = XaiProfile(original)
    with profile.lock():
        profile.set_generation(old.generation)
        profile.save(old)

    copied = tmp_path / "interrupted-copy"
    shutil.copytree(original, copied)
    damaged = XaiProfile(copied)
    with damaged.lock():
        if interrupted_side == "generation":
            damaged.set_generation("new-gen")
        else:
            damaged.save(XaiCredential("new-access", "new-refresh", time.time() + 3600, "new-gen"))
    broker = XaiAuthBroker(copied)
    try:
        status = broker.status()
        assert status["connection_state"] == "HOLD"
        assert status["reason_code"] == "XAI_AUTH_GENERATION_MISMATCH"
        account = next(
            row for row in LocalModelCredentials(copied).account_connections()
            if row["provider"] == "xai"
        )
        assert account["connection_state"] == "HOLD"
        assert account["execution_eligible"] is False
        with pytest.raises(XaiProfileHold, match="XAI_AUTH_GENERATION_MISMATCH"):
            broker.execution_credential()
    finally:
        broker.close()
    reopened = _run(copied, "read", str(written["thread_id"]), home)
    assert reopened["result_digest"] == written["result_digest"]
    assert reopened["evidence_digest"] == written["evidence_digest"]


def test_object_missing_and_corrupt_copy_fail_without_fabricated_bytes(tmp_path: Path) -> None:
    original = tmp_path / "original"
    raw = b"synthetic original evidence bytes"
    digest = hashlib.sha256(raw).hexdigest()
    store = ContentAddressedObjectStore(original)
    store.put(raw, digest, operation_id="object-copy")
    assert store.read(digest) == raw

    missing = tmp_path / "missing-copy"
    shutil.copytree(original, missing)
    ContentAddressedObjectStore(missing).path_for(digest).unlink()
    with pytest.raises(FileNotFoundError):
        ContentAddressedObjectStore(missing).read(digest)

    corrupt = tmp_path / "corrupt-copy"
    shutil.copytree(original, corrupt)
    ContentAddressedObjectStore(corrupt).path_for(digest).write_bytes(b"wrong bytes")
    with pytest.raises(RuntimeError, match="digest verification"):
        ContentAddressedObjectStore(corrupt).read(digest)
    assert store.read(digest) == raw


def _terminate_owned_child_after_marker(
    script: str, workspace: Path, marker: Path, home: Path
) -> None:
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(workspace), str(marker)],
        env=_child_env(home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not marker.is_file() and time.monotonic() < deadline:
            if child.poll() is not None:
                break
            time.sleep(0.05)
        assert marker.is_file(), child.communicate(timeout=2)[1]
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_owned_child_interruption_before_setup_replace_preserves_old_consent(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "setup"
    saved = write_setup(WorkspaceSetupState(internet_consent="DENIED"), workspace)
    source = workspace / "workspace-setup.json"
    before = source.read_bytes()
    marker = tmp_path / "setup-before-replace.marker"
    home = tmp_path / "synthetic-home"
    home.mkdir()
    script = r"""
import sys, time
from pathlib import Path
from thoth.adapters.storage import workspace_setup as setup
from thoth.domain.workspace_setup import WorkspaceSetupState
workspace, marker = Path(sys.argv[1]), Path(sys.argv[2])
def stopped_replace(source, destination):
    marker.write_text("before-replace", encoding="utf-8")
    time.sleep(30)
setup.os.replace = stopped_replace
setup.write_setup(WorkspaceSetupState(revision=2, internet_consent="ALLOWED"), workspace)
"""
    _terminate_owned_child_after_marker(script, workspace, marker, home)
    assert source.read_bytes() == before
    restored = read_setup(workspace)
    assert restored.internet_consent == saved.internet_consent == "DENIED"
    assert restored.internet_grant_id is None


def test_owned_child_interruption_before_sqlite_commit_rolls_back(tmp_path: Path) -> None:
    from thoth.apps.runtime import create_runtime

    workspace = tmp_path / "sqlite"
    first = create_runtime(workspace)
    first.close()
    marker = tmp_path / "db-before-commit.marker"
    home = tmp_path / "synthetic-home"
    home.mkdir()
    script = r"""
import sys, time
from pathlib import Path
from thoth.apps.runtime import create_runtime
workspace, marker = Path(sys.argv[1]), Path(sys.argv[2])
runtime = create_runtime(workspace)
with runtime.ledger.engine.begin() as conn:
    sql = (
        "INSERT INTO projects (project_id,name,cutoff_at,lifecycle,policy_ref,created_at) "
        "VALUES ('project:uncommitted','Uncommitted','2026-09-13T00:00:00Z',"
        "'OPEN','policy:synthetic','2026-09-13T00:00:00Z')"
    )
    conn.exec_driver_sql(sql)
    marker.write_text("before-commit", encoding="utf-8")
    time.sleep(30)
"""
    _terminate_owned_child_after_marker(script, workspace, marker, home)
    reopened = create_runtime(workspace)
    try:
        with reopened.ledger.engine.connect() as connection:
            count = connection.exec_driver_sql(
                "SELECT count(*) FROM projects WHERE project_id='project:uncommitted'"
            ).scalar()
        assert count == 0
    finally:
        reopened.close()
