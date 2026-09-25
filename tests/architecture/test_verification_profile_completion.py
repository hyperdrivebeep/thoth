from __future__ import annotations

import io
import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import checkpoint_commit_contract as checkpoint
from scripts import complete_architecture_gate as completion
from scripts.verification_bundle_contract import current_index
from scripts.verification_status import verification_status
from tests.architecture.profile_helpers import baseline_fixture, changed_tool
from tests.architecture.progress_helpers import simulated_reports
from tests.architecture.test_portable_verification_bundle import ROOT, prepare_completion_fixture


@pytest.mark.parametrize("force_full,failure", [(False, False), (True, False), (False, True)])
def test_completion_uses_actual_profile_and_preserves_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    force_full: bool,
    failure: bool,
) -> None:
    seed = prepare_completion_fixture(tmp_path, monkeypatch, owned=True)
    baseline = baseline_fixture(tmp_path, seed)
    changed_tool(tmp_path)
    calls: list[list[str]] = []
    profile = "FULL" if force_full else "TOOLING"

    def observed(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        simulated_reports(tmp_path, kwargs["env"]["THOTH_VERIFICATION_RUN_ID"], profile=profile)
        return subprocess.CompletedProcess(args, int(failure))

    # This is a simulated child exit for protocol validation, not full product execution proof.
    monkeypatch.setattr(completion, "subprocess", SimpleNamespace(run=observed))
    if failure:
        with pytest.raises(RuntimeError, match="exit code 1"):
            completion.main([])
        index = current_index(tmp_path)
        assert index is not None and index["bundle_id"] == baseline["bundle_id"]
        assert not (tmp_path / ".thoth/architecture/verification.json").exists()
        return
    assert completion.main(["--full"] if force_full else []) == 0
    assert len(calls) == 1
    assert calls[0][-1] == ("verify" if force_full else "tooling")
    status = verification_status(tmp_path)
    assert status["verification_profile"] == profile
    assert status["current_source_verified"] is force_full
    assert status["current_scope_verified"] is True
    capsys.readouterr()
    gate = tmp_path / ".thoth/architecture"
    namespace = runpy.run_path(
        str(ROOT / ".codex/hooks/stop_acceptance_gate.py"), run_name="fixture"
    )
    namespace["main"].__globals__["ROOT"] = tmp_path
    monkeypatch.setenv("THOTH_PREFLIGHT_PATH", str(gate / "preflight.json"))
    monkeypatch.setenv("THOTH_VERIFICATION_PATH", str(gate / "verification.json"))
    for session in ("session-a", "unrelated-reader"):
        monkeypatch.setattr(
            sys,
            "stdin",
            io.TextIOWrapper(
                io.BytesIO(
                    json.dumps(
                        {
                            "session_id": session,
                            "turn_id": "later",
                        }
                    ).encode()
                )
            ),
        )
        assert namespace["main"]() == 0
        assert capsys.readouterr().out == ""
    if not force_full:
        with pytest.raises(ValueError, match="FULL_SUITE_REQUIRED"):
            checkpoint.prepare_checkpoint(
                root=tmp_path, gate_dir=gate, remote_name="origin", branch="fixture"
            )

    receipt_bytes = (gate / "verification.json").read_bytes()
    index_before = current_index(tmp_path)
    assert index_before is not None
    (tmp_path / "PROJECT_WIKI/NOW.md").write_text(
        "Ordinary status documentation.\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(
            io.BytesIO(json.dumps({"session_id": "session-a", "turn_id": "docs"}).encode())
        ),
    )
    assert namespace["main"]() == 0
    advisory = json.loads(capsys.readouterr().out)
    assert "DOCUMENTATION_REVIEW_ONLY" in advisory["systemMessage"]
    assert "continue" not in advisory
    assert current_index(tmp_path) == index_before
    assert (gate / "verification.json").read_bytes() == receipt_bytes
    assert verification_status(tmp_path)["current_source_verified"] is False
    wiki_path = gate / "wiki-sync" / (seed["preflight"]["preflight_receipt_id"] + ".json")
    wiki_bytes = wiki_path.read_bytes()
    wiki_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(
            io.BytesIO(
                json.dumps({"session_id": "session-a", "turn_id": "corrupt-doc-receipt"}).encode()
            )
        ),
    )
    assert namespace["main"]() == 0
    assert json.loads(capsys.readouterr().out)["continue"] is False
    wiki_path.write_bytes(wiki_bytes)
    (tmp_path / "source.py").write_text("product_changed = True\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(
            io.BytesIO(json.dumps({"session_id": "session-a", "turn_id": "mixed"}).encode())
        ),
    )
    assert namespace["main"]() == 0
    assert json.loads(capsys.readouterr().out)["continue"] is False


@pytest.mark.parametrize("key", ["PYTEST_ADDOPTS", "PYTEST_PLUGINS"])
def test_external_pytest_selection_cannot_override_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    prepare_completion_fixture(tmp_path, monkeypatch, owned=True)
    monkeypatch.setenv(key, "--collect-only")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("verification must not start with external selection options")

    monkeypatch.setattr(completion, "subprocess", SimpleNamespace(run=forbidden))
    with pytest.raises(ValueError, match="external pytest"):
        completion.main([])
    assert not (tmp_path / ".thoth/architecture/verification.json").exists()
