from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_focus_basis import research_host
from tests.integration.test_research_request_v2 import ControlledResearchModel, setup

from thoth.adapters.storage.resource_scope import SqliteResourceScopeStore
from thoth.application.commands.research_threads import ResearchThreadHandlers
from thoth.application.services.resource_scope_service import ResourceScopeService
from thoth.apps.runtime_types import AppRuntime
from thoth.domain.enums import EntityType
from thoth.domain.resource_scope import (
    ResourceScopeBody,
    ResourceScopeError,
    ResourceScopeReceipt,
    ResourceScopeRecord,
    seal_resource_scope,
)


def _scope_receipt(record: ResourceScopeRecord) -> ResourceScopeReceipt:
    builder: object = vars(ResourceScopeService).get("_receipt")
    assert callable(builder)
    receipt: object = builder(record)
    assert isinstance(receipt, ResourceScopeReceipt)
    return receipt


def _refs(value: object) -> Sequence[str]:
    assert isinstance(value, (list, tuple))
    assert all(isinstance(item, str) for item in cast(Sequence[object], value))
    return cast(Sequence[str], value)


async def test_large_parent_set_persists_and_shared_artifact_is_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await setup(tmp_path, ControlledResearchModel(), source=False)
    try:
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "wide.md").write_text(
            "\n".join(f"latency record {i}: 12 ms" for i in range(1870)), encoding="utf-8"
        )
        source = value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "wide",
                    {
                        "project_id": "p",
                        "relative_path": "wide.md",
                        "media_type": "text/markdown",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        host = research_host(runtime)
        assert isinstance(host, ResearchThreadHandlers)
        access = host.access
        assert isinstance(access, ResourceScopeService)
        refs = tuple(span.span_id for span in host.analysis.evidence("p"))
        assert len(refs) == 1870
        artifact = str(source["artifact"]["artifact_id"])
        reads: list[str] = []
        original = SqliteResourceScopeStore.read

        def counted(
            self: SqliteResourceScopeStore, project: str, ref: str
        ) -> ResourceScopeRecord | None:
            reads.append(ref)
            return original(self, project, ref)

        with monkeypatch.context() as patch:
            patch.setattr(SqliteResourceScopeStore, "read", counted)
            access.require_reads("p", refs)
        assert reads.count(artifact) == 1
        assert len(reads) == 1871  # 1870 missing direct aliases + one sealed source record.
        child = access.ensure_derived("p", "wide-result", refs, is_new=True)
        assert child.parent_refs == refs
        stored = SqliteResourceScopeStore(runtime.ledger.engine).read("p", "wide-result")
        assert stored == child
        with pytest.raises(ResourceScopeError):
            access.ensure_derived("p", "invalid-result", (*refs, "missing-parent"), is_new=True)
        assert SqliteResourceScopeStore(runtime.ledger.engine).read("p", "invalid-result") is None

        # A new check must inspect a newly missing receipt, not reuse prior authorization.
        def missing(
            self: SqliteResourceScopeStore, project: str, ref: str
        ) -> ResourceScopeRecord | None:
            if ref == artifact:
                raise ResourceScopeError("RESOURCE_SCOPE_RECEIPT_MISSING")
            return original(self, project, ref)

        with monkeypatch.context() as patch:
            patch.setattr(SqliteResourceScopeStore, "read", missing)
            with pytest.raises(ResourceScopeError, match="RESOURCE_SCOPE_RECEIPT_MISSING"):
                access.require_reads("p", refs)
        # A valid pair of individual seals is not proof of an acyclic parent graph.
        store = SqliteResourceScopeStore(runtime.ledger.engine)
        for name, parent in [("cycle-a", "cycle-b"), ("cycle-b", "cycle-a")]:
            body = ResourceScopeBody.model_validate(child.model_dump(exclude={"record_digest"}))
            record = seal_resource_scope(
                body.model_copy(
                    update={
                        "resource_ref": name,
                        "scope_id": name,
                        "receipt_ref": f"receipt:{name}",
                        "parent_refs": (parent,),
                    }
                )
            )
            store.append(record, _scope_receipt(record), expected_revision=0)
        with pytest.raises(ResourceScopeError, match="RESOURCE_SCOPE_LINEAGE_INVALID"):
            access.require_read("p", "cycle-a")
    finally:
        runtime.close()
    reopened = await setup_reopened(tmp_path)
    try:
        assert SqliteResourceScopeStore(reopened.ledger.engine).read("p", "wide-result") == child
    finally:
        reopened.close()


async def setup_reopened(path: Path) -> AppRuntime:
    from thoth.apps.runtime import create_runtime

    return create_runtime(path, model_resolver=ControlledResearchModel())


async def test_normal_research_publishes_all_160_selected_dependencies(tmp_path: Path) -> None:
    model = ControlledResearchModel()
    runtime = await setup(tmp_path, model, source=False)
    try:
        (tmp_path / "inbox").mkdir(exist_ok=True)
        (tmp_path / "inbox" / "wide.md").write_text(
            # Every fixture span must survive the existing short-text retrieval filter.
            "\n".join(
                f"latency record {i}: 12 ms alpha measured under the stated condition"
                for i in range(160)
            ),
            encoding="utf-8",
        )
        value(
            await runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "wide",
                    {
                        "project_id": "p",
                        "relative_path": "wide.md",
                        "media_type": "text/markdown",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        admitted = value(
            await runtime.bus.dispatch(
                request(
                    "thread/start",
                    "start",
                    {"project_id": "p", "problem": "latency 12 ms alpha", "contract_version": 2},
                )
            )
        )
        await runtime.bus.drain()
        status = value(
            await runtime.bus.query(
                request(
                    "thread/read", "read", {"project_id": "p", "thread_id": admitted["thread_id"]}
                )
            )
        )
        assert status["operation_state"] == "SUCCEEDED", status.get("operation_error")
        assert status["current_result"]["terminal_reason"] == "BOUNDED_RESEARCH_COMPLETE"
        assert len(model.calls[0].context_pack.evidence) == 160
        consumed = {span.span_id for call in model.calls for span in call.context_pack.evidence}
        operation = runtime.bus.read_operation(admitted["operation_id"])
        assert operation is not None
        assert operation.resource_uses is not None
        assert consumed <= {use.resource_ref for use in operation.resource_uses}
        host = research_host(runtime)
        assert isinstance(host, ResearchThreadHandlers)
        published = host.records.read(
            "p", EntityType.DECISION_OBJECT, f"result:{admitted['thread_id']}"
        )
        assert published is not None
        result_ref, manifest = published
        scope = SqliteResourceScopeStore(runtime.ledger.engine).read(
            "p", f"revision:{result_ref.revision_digest}"
        )
        assert scope is not None and len(_refs(manifest["source_refs"])) == 160
        assert set(_refs(manifest["source_refs"])) <= set(scope.parent_refs)
        head = status["current_result"]["request_ref"]["revision_digest"]
        assert head
        assert status["current_result"]["result"]["answer"]
    finally:
        runtime.close()
