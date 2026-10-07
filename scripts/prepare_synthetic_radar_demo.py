"""Prepare the synthetic radar demo in a THOTH workspace, through the public methods only.

Usage (the API of a new, empty workspace must be running; nothing is read from or written to any
other workspace):

    python scripts/prepare_synthetic_radar_demo.py phase1 --api http://127.0.0.1:8861
    python scripts/prepare_synthetic_radar_demo.py phase2 --api http://127.0.0.1:8861

phase1 makes a project, connects the requirement, test-plan and dry-weather result files as
project materials (the same way a person uses "파일 자료 연결"), and writes the phase 1 trace CSV.
The CSV's "근거 위치" are the ids of the sentences THOTH read from the connected result file, so
choosing one in the trace table highlights that sentence. The person then imports the CSV on the
trace page ("새로 만들기").

phase2 connects the rain result file to the same project and writes the CSV that adds the rain
results ("기존 표 고치기"). It only writes a file; the import is done on the trace page.

The demo files are invented example data. This script sends them as they are, under their own
names (.yaml files are read as plain text). It never starts a model run.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from thoth.application.services.trace_csv import export_rows, parse_csv, write_csv
from thoth.domain.verification_trace import (
    Comparator,
    CriterionRule,
    ResultRecord,
    TraceItem,
    TraceKind,
    TraceLink,
    TraceRelation,
    TraceSet,
)

NOTICE = "SYNTHETIC DEMO DATA - NOT MEASURED - NOT APPROVED - NOT FOR ENGINEERING USE"
DEFAULT_DEMO = Path("examples/synthetic-radar-demo-v1")
DEFAULT_OUT = Path("outputs/b5-demo/files")
REQUIREMENT_FILE = "10_REQUIREMENTS_SYNTHETIC.yaml"
TEST_PLAN_FILE = "20_TEST_PLAN_SYNTHETIC.yaml"
RESULT_PARTS = ("detection", "false_track")
STATE_NAME = "state.json"

# One call to the server: (path, body, headers) -> the decoded JSON answer.
Transport = Callable[[str, bytes, dict[str, str]], dict[str, Any]]


class DemoError(Exception):
    pass


def http_transport(base: str) -> Transport:
    def send(path: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(base.rstrip("/") + path, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise DemoError(f"{path}: HTTP {error.code} {error.read()[:200]!r}") from error

    return send


class Api:
    def __init__(self, transport: Transport) -> None:
        self._send = transport

    def rpc(self, method: str, params: dict[str, Any], *, query: bool = False) -> dict[str, Any]:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": str(uuid4()),
                "method": method,
                "params": {"_meta": {"idempotencyKey": str(uuid4())}, "input": params},
            }
        ).encode()
        answer = self._send(
            "/rpc/query" if query else "/rpc", body, {"content-type": "application/json"}
        )
        if "error" in answer:
            raise DemoError(f"{method}: {json.dumps(answer['error'], ensure_ascii=False)[:300]}")
        return answer["result"]["value"]

    def stage(self, project_id: str, name: str, data: bytes) -> dict[str, Any]:
        boundary = "----thoth" + uuid4().hex
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + data
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self._send(
            "/files/stage",
            body,
            {
                "content-type": "multipart/form-data; boundary=" + boundary,
                "x-thoth-project-id": project_id,
            },
        )


# --- connecting the demo files as project materials ------------------------------------------


def connect_material(api: Api, project_id: str, path: Path) -> str:
    """Connect one demo file the way the file panel does; returns the artifact id."""
    staged = api.stage(project_id, path.name, path.read_bytes())
    connected = api.rpc(
        "project/source/connect",
        {
            "project_id": project_id,
            "relative_path": staged["relative_path"],
            "media_type": staged["media_type"],
            "authority": "UNCLASSIFIED",
            "cutoff_state": "ELIGIBLE",
            "security_class": "INTERNAL",
            "resource_scope": {"owner_kind": "PROJECT", "visibility": "PROJECT_SHARED"},
            "version_label": "synthetic-radar-demo-v1",
        },
    )
    return str(connected["artifact"]["artifact_id"])


def value_lines(text: str) -> dict[str, int]:
    """The line of the "value:" entry under detection and under false_track (first line is 1)."""
    found: dict[str, int] = {}
    section: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if line and not line[0].isspace() and line.rstrip().endswith(":"):
            section = line.rstrip()[:-1]
        elif section in RESULT_PARTS and line.strip().startswith("value:"):
            found.setdefault(section, number)
    return found


def sentence_ids(api: Api, project_id: str, artifact_id: str) -> dict[int, str]:
    """Line number -> the sentence id THOTH gave that line of the connected file."""
    listed = api.rpc("evidence/list", {"project_id": project_id}, query=True)
    return {
        int(span["locator"]["line"]): str(span["span_id"])
        for span in listed["evidence"]
        if span["artifact_id"] == artifact_id and span["locator"].get("line") is not None
    }


def result_sentence_ids(api: Api, project_id: str, artifact_id: str, path: Path) -> dict[str, str]:
    """For one result file: {"detection": sentence id, "false_track": sentence id}."""
    lines = value_lines(path.read_text(encoding="utf-8"))
    sentences = sentence_ids(api, project_id, artifact_id)
    missing = [part for part in RESULT_PARTS if lines.get(part) not in sentences]
    if missing:
        raise DemoError(f"{path.name}: no connected sentence for {', '.join(missing)}")
    return {part: sentences[lines[part]] for part in RESULT_PARTS}


# --- the trace set -----------------------------------------------------------------------------


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def result_files(demo: Path) -> list[Path]:
    return sorted(demo.glob("3*_RESULT_*.yaml"))


def build_trace_set(demo: Path, phase: int, spans: dict[str, dict[str, str]]) -> TraceSet:
    """The demo as a trace set.

    spans: result file name -> {"detection" / "false_track": sentence id of its value line}.
    """
    marked = {"synthetic_notice": NOTICE}
    requirement = _yaml(demo / REQUIREMENT_FILE)["requirement"]
    cases = _yaml(demo / TEST_PLAN_FILE)["test_cases"]
    items = [
        TraceItem(
            item_id=requirement["id"],
            item_key="k-req",
            kind=TraceKind.REQUIREMENT,
            title=requirement["title"],
            fields=marked,
        )
    ]
    links: list[TraceLink] = []
    rules: list[CriterionRule] = []
    for number, criterion in enumerate(requirement["criteria"]):
        cid = criterion["id"]
        items.append(
            TraceItem(
                item_id=cid,
                item_key=f"k-c{number}",
                kind=TraceKind.CRITERION,
                title=criterion["title"],
                fields=marked,
            )
        )
        links.append(
            TraceLink(
                link_id=f"L-ref-{cid}",
                from_id=cid,
                to_id=requirement["id"],
                relation=TraceRelation.REFINES,
            )
        )
        rules.append(
            CriterionRule(
                rule_id=f"R-{cid}",
                criterion_id=cid,
                measure=criterion["measure"],
                comparator=Comparator(criterion["comparator"]),
                threshold=Decimal(criterion["threshold"]),
                unit=criterion["unit"],
                condition=criterion["condition"],
                required=criterion["required"],
            )
        )
    for number, case in enumerate(cases):
        items.append(
            TraceItem(
                item_id=case["id"],
                item_key=f"k-t{number}",
                kind=TraceKind.TEST_CASE,
                title=case["title"],
                fields=marked,
            )
        )
        links.extend(
            TraceLink(
                link_id=f"L-ver-{cid}",
                from_id=cid,
                to_id=case["id"],
                relation=TraceRelation.VERIFIED_BY,
            )
            for cid in case["covers"]
        )
    results: list[ResultRecord] = []
    for path in result_files(demo):
        data = _yaml(path)
        if data["available_from_phase"] > phase:
            continue
        if path.name not in spans:
            raise DemoError(f"{path.name}: its sentences are not connected yet")
        for part in RESULT_PARTS:
            body = data[part]
            rid = f"SYN-RES-{body['criterion']}"
            items.append(
                TraceItem(item_id=rid, item_key=f"k-{rid}", kind=TraceKind.RESULT, fields=marked)
            )
            links.append(
                TraceLink(
                    link_id=f"L-prod-{rid}",
                    from_id=data["test_case"],
                    to_id=rid,
                    relation=TraceRelation.PRODUCES,
                )
            )
            results.append(
                ResultRecord(
                    result_id=rid,
                    criterion_id=body["criterion"],
                    condition=data["condition"],
                    value=Decimal(body["value"]),
                    unit=body["unit"],
                    numerator=body["numerator"],
                    denominator=body["denominator"],
                    observed_at=datetime.fromisoformat(data["observed_at"]),
                    source_span_refs=(spans[path.name][part],),
                )
            )
    return TraceSet(
        items=tuple(items), links=tuple(links), rules=tuple(rules), results=tuple(results)
    )


def _row_key(row: dict[str, str]) -> tuple[str, str]:
    return (
        row["row_type"],
        row.get("item_id") or row.get("link_id") or row.get("rule_id") or row.get("result_id", ""),
    )


def phase1_csv(demo: Path, spans: dict[str, dict[str, str]]) -> str:
    return write_csv(export_rows(build_trace_set(demo, 1, spans)))


def phase2_csv(demo: Path, spans: dict[str, dict[str, str]], current_set_digest: str) -> str:
    """Only the rows the rain results add, tied to the trace as it is now ("기존 표 고치기")."""
    already = {_row_key(row) for row in export_rows(build_trace_set(demo, 1, spans))}
    rows = [
        {**row, "base_set_digest": current_set_digest}
        for row in export_rows(build_trace_set(demo, 2, spans))
        if _row_key(row) not in already
    ]
    return write_csv(rows)


# --- the two steps -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    project_id: str
    csv_path: Path
    mode: str
    preview: dict[str, Any]


def _preview(api: Api, project_id: str, mode: str, text: str) -> dict[str, Any]:
    """Ask the server what the import would change; nothing is saved."""
    parsed = parse_csv(text)
    if parsed.issues or parsed.ignored_columns:
        raise DemoError(f"the CSV does not read cleanly: {[i.code for i in parsed.issues]}")
    preview = api.rpc(
        "trace/importPreview",
        {"project_id": project_id, "mode": mode, "csv_text": text},
        query=True,
    )
    if not preview["applicable"]:
        raise DemoError(f"the server would refuse this CSV: {preview['conflicts']}")
    return preview


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")  # the text already has its BOM and CRLF


def prepare_phase1(api: Api, demo: Path, out: Path, name: str) -> Prepared:
    project_id = f"project:web-{uuid4()}"
    api.rpc(
        "project/create",
        {
            "project_id": project_id,
            "name": name,
            "cutoff_at": datetime.now().astimezone().isoformat(),
            "overlay": "general-rnd",
        },
    )
    connect_material(api, project_id, demo / REQUIREMENT_FILE)
    connect_material(api, project_id, demo / TEST_PLAN_FILE)
    dry = next(path for path in result_files(demo) if _yaml(path)["available_from_phase"] == 1)
    spans = {
        dry.name: result_sentence_ids(api, project_id, connect_material(api, project_id, dry), dry)
    }
    text = phase1_csv(demo, spans)
    preview = _preview(api, project_id, "CREATE", text)
    csv_path = out / "trace_phase1_create.csv"
    _write(csv_path, text)
    _write(out / STATE_NAME, json.dumps({"project_id": project_id, "spans": spans}, indent=2))
    return Prepared(project_id, csv_path, "CREATE", preview)


def prepare_phase2(api: Api, demo: Path, out: Path) -> Prepared:
    state = json.loads((out / STATE_NAME).read_text(encoding="utf-8"))
    project_id: str = state["project_id"]
    spans: dict[str, dict[str, str]] = state["spans"]
    current = api.rpc("trace/read", {"project_id": project_id}, query=True)
    expected = build_trace_set(demo, 1, spans).set_digest
    if current["set_digest"] != expected:
        raise DemoError(
            "the project's trace is not the phase 1 import (import phase 1 first, unchanged)"
        )
    rain = next(path for path in result_files(demo) if _yaml(path)["available_from_phase"] == 2)
    artifact = connect_material(api, project_id, rain)
    spans[rain.name] = result_sentence_ids(api, project_id, artifact, rain)
    text = phase2_csv(demo, spans, current["set_digest"])
    preview = _preview(api, project_id, "UPDATE", text)
    csv_path = out / "trace_phase2_rain_update.csv"
    _write(csv_path, text)
    _write(out / STATE_NAME, json.dumps({"project_id": project_id, "spans": spans}, indent=2))
    return Prepared(project_id, csv_path, "UPDATE", preview)


def _summary(done: Prepared) -> str:
    found = done.preview["changes"]
    mode = "새로 만들기" if done.mode == "CREATE" else "기존 표 고치기"
    return (
        f"project: {done.project_id}\n"
        f"csv: {done.csv_path}\n"
        f"trace page > CSV 들여오기 > {mode}: "
        f"추가 {len(found['added'])} · 변경 {len(found['updated'])} · "
        f"삭제 {len(found['deleted'])} · 그대로 {found['unchanged']} "
        "(미리보기만 했고 저장하지 않았습니다)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("step", choices=("phase1", "phase2"))
    parser.add_argument(
        "--api", required=True, help="the API of the new workspace, e.g. http://127.0.0.1:8861"
    )
    parser.add_argument("--demo", type=Path, default=DEFAULT_DEMO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--name", default="합성 레이더 데모")
    args = parser.parse_args(argv)
    api = Api(http_transport(args.api))
    try:
        if args.step == "phase1":
            done = prepare_phase1(api, args.demo, args.out, args.name)
        else:
            done = prepare_phase2(api, args.demo, args.out)
    except DemoError as error:
        print(f"PROBLEM {error}", file=sys.stderr)
        return 1
    print(_summary(done))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
