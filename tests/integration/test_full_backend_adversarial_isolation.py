from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from tests.integration.scoped_runtime import create_runtime

from thoth.application.services.evidence_context import select_evidence_context
from thoth.domain.artifact import SourceLocator
from thoth.domain.enums import (
    AuthorityState,
    CutoffState,
    SupportState,
    VerificationState,
)
from thoth.domain.evidence import EvidenceSpan
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
async def test_cross_project_domain_records_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "isolation"
    inbox = workspace / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "source.md").write_text("# Evidence\n\nProject A only.\n", encoding="utf-8")
    runtime = create_runtime(workspace)
    try:
        for suffix in ("a", "b"):
            value(
                await runtime.bus.dispatch(
                    request(
                        "project/create",
                        f"isolation-project-{suffix}",
                        {
                            "project_id": f"project:isolation:{suffix}",
                            "name": f"Isolation {suffix}",
                            "cutoff_at": "2026-08-31T00:00:00Z",
                        },
                    )
                )
            )
        project_a = "project:isolation:a"
        project_b = "project:isolation:b"
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "isolation-source-a",
                    {
                        "project_id": project_a,
                        "relative_path": "source.md",
                        "media_type": "text/markdown",
                        "authority": "OFFICIAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        thread = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "isolation-thread-a",
                    {
                        "project_id": project_a,
                        "thread_id": "thread:isolation:a",
                        "problem": "Project A decision",
                        "scope": {"workstream": "a"},
                    },
                )
            )
        )
        object_id = str(cast(list[str], thread["current_object_ids"])[0])
        spans = cast(
            list[dict[str, JsonValue]],
            value(
                await runtime.bus.dispatch(
                    request(
                        "evidence/list",
                        "isolation-evidence-a",
                        {"project_id": project_a},
                    )
                )
            )["spans"],
        )
        span_id = str(spans[0]["span_id"])
        hypotheses = cast(
            list[dict[str, JsonValue]],
            value(
                await runtime.bus.dispatch(
                    request(
                        "hypothesis/generate",
                        "isolation-hypothesis-a",
                        {
                            "project_id": project_a,
                            "object_id": object_id,
                            "question": "Project A decision",
                            "evidence_scope": [span_id],
                            "generation_policy_ref": "generation:bounded-v1",
                        },
                    )
                )
            )["hypotheses"],
        )
        actions = cast(
            list[dict[str, JsonValue]],
            value(
                await runtime.bus.dispatch(
                    request(
                        "action/generate",
                        "isolation-action-a",
                        {
                            "project_id": project_a,
                            "object_id": object_id,
                            "hypothesis_refs": [hypotheses[0]["hypothesis_id"]],
                            "decision_need": "Project A only",
                            "evidence_scope": [span_id],
                        },
                    )
                )
            )["actions"],
        )
        attempts = (
            request(
                "object/read",
                "cross-object",
                {"project_id": project_b, "object_id": object_id},
            ),
            request(
                "evidence/read",
                "cross-evidence",
                {"project_id": project_b, "span_id": span_id},
            ),
            request(
                "hypothesis/read",
                "cross-hypothesis",
                {
                    "project_id": project_b,
                    "hypothesis_id": hypotheses[0]["hypothesis_id"],
                },
            ),
            request(
                "action/read",
                "cross-action",
                {"project_id": project_b, "action_id": actions[0]["action_id"]},
            ),
        )
        for cross_request in attempts:
            response = await runtime.bus.dispatch(cross_request)
            assert response.error is not None
            replay = await runtime.bus.dispatch(cross_request)
            assert replay.error == response.error
    finally:
        runtime.close()
    reopened = create_runtime(workspace)
    try:
        for cross_request in attempts:
            replay = await reopened.bus.dispatch(cross_request)
            assert replay.error is not None
        legitimate = value(
            await reopened.bus.dispatch(
                request(
                    "evidence/read",
                    "original-project-still-readable",
                    {"project_id": project_a, "span_id": span_id},
                )
            )
        )
        assert span_id in str(legitimate)
    finally:
        reopened.close()


def test_after_cutoff_evidence_cannot_be_selected_or_forced() -> None:
    eligible = EvidenceSpan(
        span_id="span:eligible",
        project_id="project:cutoff",
        artifact_id="artifact:eligible",
        source_version_id="source-version:eligible",
        locator=SourceLocator(page=1),
        exact_text="eligible evidence",
        text_sha256="a" * 64,
        extraction_method="fixture",
        support_state=SupportState.EXTRACTED,
        authority_state=AuthorityState.OFFICIAL,
        verification_state=VerificationState.PROVENANCE_VALID,
        cutoff_state=CutoffState.ELIGIBLE,
    )
    future = eligible.model_copy(
        update={
            "span_id": "span:future",
            "artifact_id": "artifact:future",
            "source_version_id": "source-version:future",
            "exact_text": "future oracle answer",
            "text_sha256": "b" * 64,
            "cutoff_state": CutoffState.AFTER_CUTOFF,
        }
    )
    selected = select_evidence_context(
        problem="future oracle answer",
        evidence=(eligible, future),
    )
    assert tuple(item.span_id for item in selected.selected) == ("span:eligible",)
    with pytest.raises(ValueError, match="forced evidence references are missing"):
        select_evidence_context(
            problem="future oracle answer",
            evidence=(eligible, future),
            forced_refs=("span:future",),
        )
