"""A real runtime's answer for the evidence panel, as the screen receives it.

The screen's check that the answer it shows is the stored answer (resultEvidence.ts in the web api)
used to be tested only with hand-made responses, so a server change in what thread/read shows went
unnoticed. This builds the responses from a run with the controlled (fake) model: thread/read and
thread/result/read as the web client asks for them (expected_result_contract v5), and the same
answer stored in the form before grounded rows, read through the server's own functions. Keys no
screen reads (the ones thread/read leaves out) are removed to keep the file small.

Regenerate the web fixture with:  python -m tests.integration.result_evidence_runtime
"""

import asyncio
import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel

from thoth.application.services.expected_result_view import result_read_view
from thoth.application.services.research_read_summary import (
    OMITTED_MANIFEST_FIELDS,
    OMITTED_RESULT_KEYS,
    summarize_result,
)
from thoth.application.services.result_shown_view import with_shown_result
from thoth.apps.runtime import create_runtime

FIXTURE = (
    Path(__file__).resolve().parents[2] / "apps/web/src/api/fixtures/result-evidence-runtime.json"
)
PROJECT = "project:shown-result"
# What the grounded-row contract added to every row; a result stored before it has none of these.
GROUNDED_ROW_KEYS = (
    "grounding",
    "source_stage",
    "source_bindings",
    "relation_assessment",
    "binding_availability",
    "target",
    "grounding_projection",
    "source_contract",
)


async def read_case(workspace: Path) -> dict[str, Any]:
    """Run one research with the controlled model and read it as the web client does."""
    runtime = create_runtime(
        workspace, model_resolver=ControlledResearchModel(one=True, tested=True)
    )
    try:
        value(
            await runtime.bus.dispatch(
                request(
                    "project/create",
                    "project",
                    {
                        "project_id": PROJECT,
                        "name": "Shown result",
                        "cutoff_at": "2026-09-13T00:00:00Z",
                    },
                )
            )
        )
        (workspace / "inbox").mkdir(exist_ok=True)
        (workspace / "inbox" / "records.html").write_text(
            '<html><head><meta property="article:published_time" content="2026-09-01T00:00:00Z" />'
            "</head><body><h1>Latency report</h1><p>Unit ms; condition alpha</p>"
            "<p>Record LAB-42: latency 12 ms</p>"
            "<p>Counter-record: not reproduced under another condition</p></body></html>",
            encoding="utf-8",
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": PROJECT,
                        "relative_path": "records.html",
                        "media_type": "text/html",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                        "resource_scope": {"owner_kind": "PROJECT", "visibility": "PROJECT_SHARED"},
                    },
                )
            )
        )
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {
                        "project_id": PROJECT,
                        "problem": "Summarize the LAB-42 latency conditions",
                        "contract_version": 2,
                        "response_language": "en",
                    },
                )
            )
        )
        await runtime.bus.drain()
        thread = accepted["thread_id"]

        async def query(method: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
            return dict(value(await runtime.bus.query(request(method, key, payload))))

        status = await query(
            "thread/read",
            "read",
            {"project_id": PROJECT, "thread_id": thread, "expected_result_contract": "v5"},
        )
        current = status["current_result"]
        base = {
            "project_id": PROJECT,
            "thread_id": thread,
            "request_revision_digest": current["request_ref"]["revision_digest"],
            "contract_version": 2,
            "expected_result_contract": "v5",
        }
        first = await query("thread/result/read", "result", base)
        digest = first["result_revision_digest"]
        evidence = await query(
            "thread/result/read",
            "evidence",
            {
                **base,
                "result_revision_digest": digest,
                "include_selected_evidence": True,
                "selected_evidence_offset": 0,
                "selected_evidence_limit": 50,
            },
        )
        return slim_case(
            {
                "selection": {
                    "projectId": PROJECT,
                    "threadId": thread,
                    "requestDigest": base["request_revision_digest"],
                    "operationId": current["operation_id"],
                    "resultDigest": digest,
                },
                "displayed": current["result"],
                "read": first,
                "evidence_page": evidence["selected_evidence"],
            }
        )
    finally:
        runtime.close()


ENVELOPE = ("thread_id", "request_epoch", "contract_version", "terminal_reason")


def slim_case(case: dict[str, Any]) -> dict[str, Any]:
    slim = copy.deepcopy(case)
    read = slim["read"]
    for body in (read["result"], read["manifest"]["result"]):
        for key in OMITTED_RESULT_KEYS:
            body.pop(key, None)
    for key in OMITTED_MANIFEST_FIELDS:
        if key != "source_basis":  # the screen checks every cited source against it
            read["manifest"].pop(key, None)
    read["shown_result"] = summarize_result(read["result"])
    return slim


def evidence_read(case: dict[str, Any]) -> dict[str, Any]:
    """The thread/result/read answer that carries the selected-evidence page."""
    return {**case["read"], "selected_evidence": case["evidence_page"]}


def _items(owner: Any, key: str) -> list[Any]:
    """The list under key of a dict, or an empty list for anything else."""
    if not isinstance(owner, dict):
        return []
    found: Any = cast("dict[str, Any]", owner).get(key)
    return cast("list[Any]", found) if isinstance(found, list) else []


def stored_before_grounded_rows(case: dict[str, Any]) -> dict[str, Any]:
    """The same answer as a result stored before grounded rows would be read at contract v5.

    The stored manifest result has no grounded-row keys; the server's read view adds them to the
    result it returns and to what thread/read shows, and leaves the manifest as it was stored.
    """
    legacy = copy.deepcopy(case)
    read = legacy["read"]
    stored = read["manifest"]["result"]
    stored.pop("hypothesis_contract_version", None)
    for hypothesis in _items(stored.get("portfolio"), "hypotheses"):
        for test in _items(hypothesis, "discriminating_tests"):
            for row in _items(test, "expected_by_hypothesis"):
                for key in GROUNDED_ROW_KEYS:
                    row.pop(key, None)
    envelope = {k: read["result"][k] for k in ENVELOPE if k in read["result"]}
    read["result"] = {**copy.deepcopy(stored), **envelope}
    for key in ("expected_result_view", "shown_result", "projected_keys"):
        read.pop(key, None)
    legacy["read"] = with_shown_result(result_read_view(read, "v5"))
    legacy["displayed"] = legacy["read"]["shown_result"]
    return legacy


def build_fixture(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_by": "python -m tests.integration.result_evidence_runtime",
        "cases": {
            "stored_current": current,
            "stored_before_grounded_rows": stored_before_grounded_rows(current),
        },
    }


def expected_displayed(case: dict[str, Any]) -> dict[str, Any]:
    """What thread/read shows for this case's result: the summary view of the result read."""
    return summarize_result(case["read"]["result"])


async def main(target: Path) -> None:
    with tempfile.TemporaryDirectory() as directory:
        current = await read_case(Path(directory))
    target.write_text(
        json.dumps(build_fixture(current), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE))
