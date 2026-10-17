"""Criterion preparation preserves owner reads, refusal order and identity allocation."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tests.atomicity.harness import assert_phase_delta, snapshot
from tests.integration.test_a09_storage_atomicity import compile_contract, prepare
from tests.integration.test_research_memory_basis import Boundary
from tests.unit.test_cycle_staging_contract import STAGE, subject

from thoth.adapters.storage.criterion_contract import SqliteCriterionContractStore
from thoth.application.services.criterion_projection import criterion_projection
from thoth.application.workflows.thread_cycle import ThreadCycleCommand, ThreadCycleService
from thoth.domain.enums import EntityType
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.research_execution import ResearchWork, research_work
from thoth.domain.research_identity import ResearchIdentityError
from thoth.domain.research_request import RevisionRef
from thoth.domain.resource_scope import ResourceScopeError
from thoth.domain.revision import EntitySnapshot, SemanticRevision, StagedRevision

CRITERIA = cast(
    Callable[..., tuple[StagedRevision, ...]], vars(ThreadCycleService)["_stage_criteria"]
)


@pytest.mark.parametrize(
    "case",
    (
        "owned",
        "project-mismatch",
        "head-mismatch",
        "revision-missing",
        "snapshot-missing",
        "wrong-record",
        "view-mismatch",
        "read-denied",
        "legacy-new",
        "legacy-existing",
        "legacy-binding-required",
    ),
)
async def test_criterion_owner_read_order_and_legacy_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    runtime, project, spans = await prepare(tmp_path)
    try:
        await compile_contract(runtime, project, spans)
        contracts = SqliteCriterionContractStore(
            runtime.ledger.engine, ledger=runtime.ledger
        ).list_contracts(project)
        assert contracts
        owned = criterion_projection(contracts[0])
        assert owned.source_contract_revision is not None
        key = "CRITERION:" + owned.criterion_id
        assert runtime.ledger.read_heads(project)[key] == owned.source_contract_revision
        service, prototype, events = subject()
        vars(service)["_ledger"] = runtime.ledger
        command = replace(prototype, project_id=project)
        legacy = owned.model_copy(update={"source_contract_revision": None})
        historical = STAGE(
            service,
            command,
            EntityType.CRITERION,
            legacy.criterion_id,
            legacy.model_dump(mode="python"),
            (),
            {},
        )
        events.clear()
        candidate = owned
        heads = {key: owned.source_contract_revision}
        if case == "project-mismatch":
            candidate = owned.model_copy(update={"project_id": "foreign-project"})
        elif case == "head-mismatch":
            heads[key] = "f" * 64
        elif case == "view-mismatch":
            candidate = owned.model_copy(update={"name": owned.name + " changed view"})
        elif case == "wrong-record":
            candidate = owned.model_copy(
                update={"source_contract_revision": historical.revision.revision_digest}
            )
            heads[key] = historical.revision.revision_digest
        elif case.startswith("legacy-"):
            candidate = legacy
            if case == "legacy-new":
                heads = {}
            elif case == "legacy-existing":
                heads[key] = historical.revision.revision_digest
        command = replace(command, criteria=(candidate,))
        original_revision = runtime.ledger.read_revision_by_digest
        original_snapshot = runtime.ledger.read_snapshot
        stage_calls: list[StagedRevision] = []

        def read_revision(owner: str, digest: str) -> SemanticRevision | None:
            assert owner == project and digest == heads[key]
            events.append("read:revision")
            if case == "read-denied":
                raise ResourceScopeError("FIXTURE_SCOPE_DENIED")
            if case == "revision-missing":
                return None
            if case in {"wrong-record", "legacy-existing"}:
                return historical.revision
            return original_revision(owner, digest)

        def read_snapshot(identifier: str) -> EntitySnapshot | None:
            events.append("read:snapshot")
            if case == "snapshot-missing":
                return None
            if case in {"wrong-record", "legacy-existing"}:
                assert identifier == historical.snapshot.snapshot_id
                return historical.snapshot
            return original_snapshot(identifier)

        def stage(
            current: ThreadCycleCommand,
            entity: EntityType,
            identifier: str,
            content: dict[str, object],
            evidence: tuple[EvidenceSpan, ...],
            current_heads: dict[str, str],
        ) -> StagedRevision:
            events.append("stage")
            assert current is command and current_heads is heads
            assert entity is EntityType.CRITERION and identifier == candidate.criterion_id
            assert content == candidate.model_dump(mode="python") and evidence is command.evidence
            result = STAGE(service, current, entity, identifier, content, evidence, current_heads)
            stage_calls.append(result)
            return result

        before = snapshot(runtime.ledger.engine)
        work = ResearchWork(
            RevisionRef(
                project_id=project,
                entity_type="THREAD",
                entity_id="request:fixture",
                revision_id="revision:fixture",
                revision_digest="a" * 64,
            ),
            "criterion fixture",
            Boundary(),
        )
        token = research_work.set(work)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(runtime.ledger, "read_revision_by_digest", read_revision)
                patch.setattr(runtime.ledger, "read_snapshot", read_snapshot)
                patch.setattr(service, "_stage", stage)
                if case in {"owned", "legacy-new", "legacy-existing"}:
                    result = CRITERIA(service, command, heads)
                    assert result == tuple(stage_calls)
                elif case == "read-denied":
                    with pytest.raises(ResourceScopeError, match="FIXTURE_SCOPE_DENIED"):
                        CRITERIA(service, command, heads)
                else:
                    reason = {
                        "project-mismatch": "CRITERION_PROJECT_SCOPE_MISMATCH",
                        "head-mismatch": "CRITERION_OWNER_REVISION_CHANGED",
                        "revision-missing": "CRITERION_OWNER_MISSING",
                        "snapshot-missing": "CRITERION_OWNER_MISSING",
                        "wrong-record": "CRITERION_OWNER_VIEW_MISMATCH",
                        "view-mismatch": "CRITERION_OWNER_VIEW_MISMATCH",
                        "legacy-binding-required": "CRITERION_OWNER_BINDING_REQUIRED",
                    }[case]
                    with pytest.raises(ResearchIdentityError, match=reason):
                        CRITERIA(service, command, heads)
        finally:
            research_work.reset(token)
        expected = (
            []
            if case in {"project-mismatch", "head-mismatch", "legacy-new"}
            else ["read:revision"]
            if case in {"revision-missing", "read-denied"}
            else ["read:revision", "read:snapshot"]
        )
        if case in {"legacy-new", "legacy-existing"}:
            expected += ["stage", "id:snapshot:3", "id:revision:4", "clock"]
            assert len(stage_calls) == 1
            assert stage_calls[0].revision.parent_revision_digests == (
                () if case == "legacy-new" else (historical.revision.revision_digest,)
            )
        else:
            assert stage_calls == []
        assert events == expected
        assert work.consumed_heads == (
            {key: owned.source_contract_revision} if case == "owned" else {}
        )
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


async def test_earlier_legacy_ids_remain_consumed_when_later_owned_criterion_is_rejected(
    tmp_path: Path,
) -> None:
    """A read-seam-free legacy value is staged only in memory, not published to the database."""
    runtime, project, spans = await prepare(tmp_path)
    try:
        await compile_contract(runtime, project, spans)
        record = SqliteCriterionContractStore(
            runtime.ledger.engine, ledger=runtime.ledger
        ).list_contracts(project)[0]
        owned = criterion_projection(record)
        legacy = owned.model_copy(
            update={"criterion_id": "criterion:legacy-loop", "source_contract_revision": None}
        )
        service, prototype, events = subject()
        vars(service)["_ledger"] = runtime.ledger
        command = replace(prototype, project_id=project, criteria=(legacy, owned))
        before = snapshot(runtime.ledger.engine)
        with pytest.raises(ResearchIdentityError, match="CRITERION_OWNER_REVISION_CHANGED"):
            CRITERIA(service, command, {"CRITERION:" + owned.criterion_id: "f" * 64})
        assert events == ["id:snapshot:1", "id:revision:2", "clock"]
        assert_phase_delta(before, snapshot(runtime.ledger.engine))
    finally:
        runtime.close()


async def test_normal_failure_keeps_prior_consumed_basis_but_publishes_no_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.storage_coverage_helpers import request, value
    from tests.integration.test_research_focus_basis import research_host
    from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

    from thoth.application.workflows.thread_cycle import ThreadCycleResult
    from thoth.domain.enums import ModelRole, OperationState, ThreadExecutionState

    model = ControlledResearchModel(one=True, tested=True)
    runtime = await setup(tmp_path, model)
    spans = [span.span_id for span in research_host(runtime).analysis.evidence("p")]
    await compile_contract(runtime, "p", spans)
    original = ThreadCycleService.execute
    observed: list[ResearchWork] = []
    expected_basis: dict[str, str] = {}

    async def execute(
        service: ThreadCycleService, command: ThreadCycleCommand
    ) -> ThreadCycleResult:
        record = SqliteCriterionContractStore(
            runtime.ledger.engine, ledger=runtime.ledger
        ).list_contracts("p")[0]
        owned = criterion_projection(record)
        assert owned.source_contract_revision is not None
        expected_basis["CRITERION:" + owned.criterion_id] = owned.source_contract_revision
        foreign = owned.model_copy(
            update={"project_id": "foreign", "criterion_id": "criterion:foreign"}
        )
        work = research_work.get()
        assert work is not None
        try:
            return await original(service, replace(command, criteria=(owned, foreign)))
        finally:
            assert all(
                work.consumed_heads.get(key) == digest for key, digest in expected_basis.items()
            )
            observed.append(work)

    monkeypatch.setattr(ThreadCycleService, "execute", execute)
    try:
        accepted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "criterion-loop-failure",
                    {
                        "project_id": "p",
                        "problem": "Compare the connected condition",
                        "contract_version": 2,
                    },
                )
            )
        )
        await runtime.bus.drain()
        operation_id, thread_id = accepted["operation_id"], accepted["thread_id"]
        assert isinstance(operation_id, str) and isinstance(thread_id, str)
        operation = runtime.bus.read_operation(operation_id)
        assert operation is not None and operation.state is OperationState.FAILED
        assert len(observed) == 1 and research_work.get() is None
        assert all(
            observed[0].consumed_heads.get(key) == digest for key, digest in expected_basis.items()
        )
        thread = research_host(runtime).threads.read(thread_id)
        assert thread is not None and thread.execution_state is ThreadExecutionState.IDLE
        assert not any(
            key.startswith(("HYPOTHESIS:", "ACTION:")) for key in runtime.ledger.read_heads("p")
        )
        assert all(call.role is not ModelRole.HYPOTHESIS_GENERATOR for call in model.calls)
    finally:
        runtime.close()
