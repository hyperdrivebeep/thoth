"""Let the fixture author incorporate its draft before handing it to another actor."""

from tests.integration.storage_coverage_helpers import request, value

from thoth.apps.runtime import AppRuntime


async def publish_fixture_problem(runtime: AppRuntime, project_id: str) -> None:
    evidence = value(
        await runtime.bus.dispatch(
            request(
                "evidence/list",
                "fixture-publish-evidence",
                {"project_id": project_id},
            )
        )
    )
    refs = [item["span_id"] for item in evidence["spans"]]
    assert refs
    heads = runtime.ledger.read_heads(project_id)
    key = next(key for key in heads if key.startswith("DECISION_OBJECT:"))
    value(
        await runtime.bus.dispatch(
            request(
                "object/frame/revise",
                "fixture-publish-problem",
                {
                    "project_id": project_id,
                    "object_id": key.removeprefix("DECISION_OBJECT:"),
                    "expected_revision_digest": heads[key],
                    "frame_patch": {"purpose_statement": "source-bound fixture comparison"},
                    "evidence_refs": refs,
                    "reason": "fixture author incorporates its draft into source-bound research",
                },
            )
        )
    )
