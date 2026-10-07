"""The demo preparation script against a real app: it uses only the public methods and the file
upload, and what it writes can be imported on the trace page and behaves as the demo is meant to."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from thoth.adapters.http.app import create_app

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "scripts" / "prepare_synthetic_radar_demo.py"
DEMO = REPO / "examples" / "synthetic-radar-demo-v1"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("prepare_synthetic_radar_demo", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prepare = _load()


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "workspace"))
    with TestClient(create_app()) as client:

        def send(path: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
            response = client.post(path, content=body, headers=headers)
            if response.status_code >= 400:
                raise prepare.DemoError(
                    f"{path}: HTTP {response.status_code} {response.text[:200]}"
                )
            return response.json()

        yield prepare.Api(send)


def apply_import(api: Any, project: str, mode: str, path: Path) -> dict[str, Any]:
    """What the trace page does: preview, then apply exactly what was previewed."""
    text = path.read_bytes().decode("utf-8").removeprefix("\ufeff")  # the browser drops the mark
    preview = api.rpc(
        "trace/importPreview",
        {"project_id": project, "mode": mode, "csv_text": text},
        query=True,
    )
    assert preview["applicable"], preview["conflicts"]
    return api.rpc(
        "trace/importApply",
        {
            "project_id": project,
            "mode": mode,
            "csv_text": text,
            "preview_id": preview["preview_id"],
            "input_sha256": preview["input_sha256"],
        },
    )


def states(api: Any, project: str) -> dict[str, str]:
    view = api.rpc("trace/read", {"project_id": project}, query=True)
    return {verdict["subject_id"]: verdict["state"] for verdict in view["verdicts"]}


def sentences(api: Any, project: str) -> dict[str, str]:
    listed = api.rpc("evidence/list", {"project_id": project}, query=True)
    return {span["span_id"]: span["exact_text"] for span in listed["evidence"]}


def test_phase_1_connects_the_files_and_the_csv_points_at_real_sentences(
    api: Any, tmp_path: Path
) -> None:
    done = prepare.prepare_phase1(api, DEMO, tmp_path / "files", "demo")
    assert done.mode == "CREATE" and done.preview["applicable"]
    sources = api.rpc("project/source/list", {"project_id": done.project_id}, query=True)
    assert len(sources["artifacts"]) == 3  # requirements, test plan, dry result; no rain yet
    listed = json.dumps(sources)
    assert "10_REQUIREMENTS_SYNTHETIC.yaml" in listed and "30_RESULT_DRY_SYNTHETIC.yaml" in listed
    assert ".txt" not in listed  # the files keep their own names
    apply_import(api, done.project_id, "CREATE", done.csv_path)
    view = api.rpc("trace/read", {"project_id": done.project_id}, query=True)
    found = sentences(api, done.project_id)
    refs = {r["criterion_id"]: r["source_span_refs"] for r in view["results"]}
    assert set(refs) == {"SYN-C-DET-DRY", "SYN-C-FA-DRY"}
    assert [found[ref] for ref in refs["SYN-C-DET-DRY"]] == ['value: "0.95"']
    assert [found[ref] for ref in refs["SYN-C-FA-DRY"]] == ['value: "0.30"']
    assert all(item["fields"]["synthetic_notice"] == prepare.NOTICE for item in view["items"])
    assert states(api, done.project_id) == {
        "SYN-C-DET-DRY": "PASS_COMPUTED",
        "SYN-C-FA-DRY": "PASS_COMPUTED",
        "SYN-C-DET-RAIN": "HOLD_NO_RESULT",
        "SYN-C-FA-RAIN": "HOLD_NO_RESULT",
        "SYN-C-DET-FOG": "HOLD_NO_RESULT",
        "SYN-C-FA-FOG": "HOLD_NO_RESULT",
        "SYN-REQ-RAD-001": "HOLD_INCOMPLETE",
    }


def test_phase_2_adds_only_the_rain_rows_and_the_requirement_fails_with_incomplete_coverage(
    api: Any, tmp_path: Path
) -> None:
    out = tmp_path / "files"
    first = prepare.prepare_phase1(api, DEMO, out, "demo")
    apply_import(api, first.project_id, "CREATE", first.csv_path)
    second = prepare.prepare_phase2(api, DEMO, out)
    assert second.project_id == first.project_id and second.mode == "UPDATE"
    changes = second.preview["changes"]
    assert changes["updated"] == [] and changes["deleted"] == []
    assert sorted(change["kind"] for change in changes["added"]) == sorted(
        ["ITEM", "ITEM", "LINK", "LINK", "RESULT", "RESULT"]
    )
    applied = apply_import(api, first.project_id, "UPDATE", second.csv_path)
    assert {change["subject_id"] for change in applied["verdict_changes"]} == {
        "SYN-C-DET-RAIN",
        "SYN-C-FA-RAIN",
        "SYN-REQ-RAD-001",
    }
    assert states(api, first.project_id) == {
        "SYN-C-DET-DRY": "PASS_COMPUTED",
        "SYN-C-FA-DRY": "PASS_COMPUTED",
        "SYN-C-DET-RAIN": "FAIL_COMPUTED",
        "SYN-C-FA-RAIN": "FAIL_COMPUTED",
        "SYN-C-DET-FOG": "HOLD_NO_RESULT",
        "SYN-C-FA-FOG": "HOLD_NO_RESULT",
        "SYN-REQ-RAD-001": "FAIL_WITH_INCOMPLETE_COVERAGE",
    }
    view = api.rpc("trace/read", {"project_id": first.project_id}, query=True)
    found = sentences(api, first.project_id)
    rain = {r["criterion_id"]: found[r["source_span_refs"][0]] for r in view["results"]}
    assert rain["SYN-C-DET-RAIN"] == 'value: "0.80"' and rain["SYN-C-FA-RAIN"] == 'value: "0.70"'
    exported = api.rpc("trace/export", {"project_id": first.project_id}, query=True)
    header = exported["csv_text"].removeprefix("\ufeff").splitlines()[0].split(",")
    assert header[-1] == "synthetic_notice"
    again = api.rpc(
        "trace/importPreview",
        {"project_id": first.project_id, "mode": "UPDATE", "csv_text": exported["csv_text"]},
        query=True,
    )
    assert again["applicable"] and not any(
        again["changes"][k] for k in ("added", "updated", "deleted")
    )


def test_phase_2_refuses_a_project_whose_trace_is_not_the_untouched_phase_1(
    api: Any, tmp_path: Path
) -> None:
    out = tmp_path / "files"
    prepare.prepare_phase1(api, DEMO, out, "demo")  # prepared but never imported
    with pytest.raises(prepare.DemoError, match="phase 1"):
        prepare.prepare_phase2(api, DEMO, out)


def test_the_files_are_sent_unchanged_and_the_state_names_only_ids(
    api: Any, tmp_path: Path
) -> None:
    out = tmp_path / "files"
    done = prepare.prepare_phase1(api, DEMO, out, "demo")
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert state["project_id"] == done.project_id
    assert set(state["spans"]) == {"30_RESULT_DRY_SYNTHETIC.yaml"}
    sent = {text for text in sentences(api, done.project_id).values()}
    assert f"notice: {prepare.NOTICE}" in sent  # the file's own notice line arrived as written
