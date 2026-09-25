import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel
from tests.integration.test_a02_autonomous_acquisition import prepare_thread, request, value
from tests.integration.test_research_focus_basis import research_host

from thoth.application.services.research_basis_capture import (
    capture_rendered_memory,
    render_analysis_memory,
)
from thoth.application.services.research_models import ResearchModel
from thoth.domain.canonical import canonical_payload, domain_digest, head_set_digest, model_digest
from thoth.domain.enums import MemoryPayloadMode, ModelRole, RecallEligibility
from thoth.domain.memory import FullMemoryRevision, MemoryRecord
from thoth.domain.model import ContextPack, ModelRequest, ModelResult
from thoth.domain.model_dispatch import (
    CONTROLLED_MODEL_CONTROL,
    ModelControlCapability,
    ModelReceiveObservation,
)
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_request import RevisionRef


class Answer(BaseModel):
    ok: bool = True


class Boundary:
    def new_model_call(self) -> str:
        return "controlled-memory-basis"

    def check(self) -> None:
        pass

    def call_timeout(self) -> None:
        return None

    def reserve(self, payload_bytes: int, output_tokens: int = 0) -> None:
        raise AssertionError("unexpected reservation")

    def transport(self, payload_bytes: int) -> None:
        raise AssertionError("unexpected transport")

    def owns_attempt(self) -> bool:
        raise AssertionError("unexpected attempt claim")

    def reserve_dispatch(
        self,
        dispatch_id: str,
        payload: bytes,
        output_tokens: int,
        capability: ModelControlCapability,
    ) -> None:
        raise AssertionError("unexpected model dispatch")

    def record_usage(
        self,
        dispatch_id: str,
        received_bytes: int,
        input_tokens: int | None,
        output_tokens: int | None,
        remote_stop: str,
        response_id: str | None,
        observation: ModelReceiveObservation | None = None,
        retry_of_dispatch_id: str | None = None,
        cached_input_tokens: int | None = None,
    ) -> None:
        raise AssertionError("unexpected usage recording")


def _record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _candidate(
    source: FullMemoryRevision, *, memory_id: str, project_id: str
) -> MemoryRecord:
    payload_mode = (
        MemoryPayloadMode.MEMORY_ASSERTION
        if source.assertion is not None
        else MemoryPayloadMode.DOMAIN_REFERENCE
    )
    draft: dict[str, object] = {
        "memory_id": memory_id,
        "project_id": project_id,
        "payload_mode": payload_mode.value,
        "kind": source.kind.value,
        "owner_revision_ref": source.owner_revision_ref,
        "source_ref": source.source_ref,
        "assertion": source.assertion,
        "recall_eligibility": RecallEligibility.WORKING_CONTEXT.value,
    }
    return MemoryRecord.model_validate(
        {
            **draft,
            "revision_digest": domain_digest("A06_TEST_MEMORY", "1.0.0", canonical_payload(draft)),
        }
    )


class RecordingModel:
    control_capability = CONTROLLED_MODEL_CONTROL

    def __init__(self) -> None:
        self.inputs: list[ContextPack] = []

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        self.inputs.append(request.context_pack)
        result = request.output_model.model_validate(Answer())
        return ModelResult(
            output=result,
            model_id="CONTROLLED",
            scripted=True,
            prompt_version=request.prompt_version,
            input_digest=model_digest("INPUT", request.context_pack, schema_version="1.0.0"),
            output_digest=model_digest("OUTPUT", result, schema_version="1.0.0"),
        )


async def test_only_two_actually_rendered_memory_subsets_enter_consumed_basis(
    tmp_path: Path,
) -> None:
    runtime, _connector, project = await prepare_thread(tmp_path, allow_connector=True)
    try:
        first = _record(value(
            await runtime.bus.dispatch(
                request(
                    "thread/input",
                    "seed-memories",
                    {"project_id": project, "thread_id": f"thread:{project}"},
                )
            )
        ))
        memories = tuple(
            FullMemoryRevision.model_validate_json(json.dumps(_record(item)), strict=True)
            for item in _list(_record(first["full_project_memory"])["committed"])
        )
        assert memories
        from thoth.adapters.storage import SqliteMemoryStore

        source = memories[0]
        extra = tuple(
            _candidate(
                source,
                memory_id=f"memory:distinct-consumer:{index}",
                project_id=project,
            )
            for index in range(2)
        )
        store = SqliteMemoryStore(runtime.ledger.engine)
        for candidate in extra:
            store.add(candidate)
        memory_service = research_host(runtime).analysis.memory
        promoted = await memory_service.promote_thread_results(
            project_id=project,
            thread_id=source.origin_thread_id,
            cutoff_at=source.cutoff_at,
            memory_ids=tuple(candidate.memory_id for candidate in extra),
            scope=source.scope,
        )
        memories = (*memories, *promoted.committed)
        assert len(memories) >= 3
        one, two, excluded = memories[:3]
        work = ResearchWork(
            RevisionRef(
                project_id=project,
                entity_type="THREAD",
                entity_id="request:fixture",
                revision_id="request:fixture",
                revision_digest="a" * 64,
            ),
            "Question",
            Boundary(),
        )
        work.observe_context = lambda context: capture_rendered_memory(context, runtime.ledger)
        context = ContextPack(
            case_id="memory-basis",
            project_id=project,
            object_id="fixture",
            problem="Question",
            evidence=(),
            criteria=(),
            sufficiency=None,
            input_head_set_digest=head_set_digest(runtime.ledger.read_heads(project)),
        )
        underlying = RecordingModel()
        model = ResearchModel(underlying)
        token = research_work.set(work)
        try:
            # Built/rendered elsewhere but not passed to structured(): never consumed.
            render_analysis_memory(work, (excluded,))
            assert work.memory_revision_refs == []
            first_context = context.model_copy(
                update={
                    "research_context": {
                        "authorized_project_memory": {
                            "included": [one.model_dump(mode="json")],
                            "excluded_memory_refs": [excluded.revision_digest],
                        }
                    }
                }
            )
            await model.structured(
                ModelRequest(
                    role=ModelRole.RESEARCH_PLANNER,
                    project_id=project,
                    cutoff_at=one.cutoff_at,
                    context_pack=first_context,
                    output_model=Answer,
                    prompt_version="test",
                    model_policy_ref="test",
                    max_output_tokens=100,
                )
            )
            assert work.memory_revision_refs == [one.revision_digest]
            problem = "Question" + render_analysis_memory(work, (two,))
            assert work.memory_revision_refs == [one.revision_digest]
            second_context = context.model_copy(
                update={"problem": problem, "research_context": dict(work.context)}
            )
            await model.structured(
                ModelRequest(
                    role=ModelRole.HYPOTHESIS_GENERATOR,
                    project_id=project,
                    cutoff_at=two.cutoff_at,
                    context_pack=second_context,
                    output_model=Answer,
                    prompt_version="test",
                    model_policy_ref="test",
                    max_output_tokens=100,
                )
            )
            assert work.memory_revision_refs == [one.revision_digest, two.revision_digest]
            assert excluded.revision_digest not in work.memory_revision_refs
            assert len(underlying.inputs) == 2
        finally:
            research_work.reset(token)
    finally:
        runtime.close()
