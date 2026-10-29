"""Synthetic encoders and sources only; no downloaded weights or provider calls."""

import asyncio
import ctypes
import hashlib
import os
import socket
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tests.integration.test_artifact_row_codec_contract import STAMP, document

from thoth.adapters.runtime import SystemClock, UuidIdGenerator
from thoth.adapters.storage.bundle import SqliteStoreBundle, SqliteStoreFactory
from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.application.services.scoped_artifacts import ScopedArtifactLedger
from thoth.apps.resource_scope_composition import create_resources
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.embedding_index import EmbeddingBudget, EmbeddingCapability, EmbeddingVector
from thoth.domain.evidence import EvidenceSpan
from thoth.domain.governance import ProjectPolicy, SourceBinding
from thoth.domain.project import Project
from thoth.domain.resource_scope import ResourceScopeTemplate
from thoth.domain.retrieval_embedding import DenseModelPin

PROJECT = "project:codec"


def process_is_gone(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return True
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


def unit_vector(text: str) -> tuple[float, ...]:
    position = hashlib.sha256(text.encode()).digest()[0]
    return tuple(1.0 if i == position else 0.0 for i in range(384))


class ChildEncoder:
    def encode_one(
        self, text: str, *, role: Literal["QUERY", "PASSAGE"], model: DenseModelPin
    ) -> tuple[float, ...]:
        if text.startswith("sleep:"):
            time.sleep(float(text.split(":")[1]))
        if text == "crash":
            os._exit(23)
        if text == "bad":
            return (0.0,) * 384
        if text == "network":
            socket.create_connection(("127.0.0.1", 9))
        if text == "memory":
            # Deliberately exceed the approved Job commit cap in this owned child only.
            bytearray(600 * 1024 * 1024)
        return unit_vector(text)


class FakeWorker:
    capability = EmbeddingCapability(process_tree="WINDOWS_JOB", memory_limit="COMMIT")
    budget = EmbeddingBudget()

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.closed = False

    async def encode_one(self, text: str, *, role: Literal["QUERY", "PASSAGE"]) -> EmbeddingVector:
        self.calls.append(text)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        return EmbeddingVector.from_values(unit_vector(text))

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.close()


@dataclass
class IndexFixture:
    stores: SqliteStoreBundle
    service: EmbeddingIndexService
    worker: FakeWorker

    def ingest(self, name: str = "a") -> EvidenceSpan:
        doc, span = document(name, None)
        with self.stores.immediate_uow.transaction():
            self.stores.artifacts.persist_ingestion(doc, span.source_version_id, (span,))
            assert isinstance(self.service.artifacts, ScopedArtifactLedger)
            self.service.artifacts.scopes.assign_legacy(
                PROJECT,
                span.artifact_id,
                expected_revision=0,
                template=ResourceScopeTemplate(owner_kind="PROJECT", visibility="PROJECT_SHARED"),
                reason="Synthetic explicitly shared fixture",
            )
            self.stores.governance.put_source_binding(
                SourceBinding(
                    binding_id=f"binding:{name}",
                    project_id=PROJECT,
                    artifact_id=span.artifact_id,
                    created_at=STAMP,
                    updated_at=STAMP,
                )
            )
        return span


def fixture(path: Path, *, enabled: bool = True) -> IndexFixture:
    stores = SqliteStoreFactory().open(path)
    stores.projects.create(
        Project(
            project_id=PROJECT,
            name="Synthetic",
            cutoff_at=STAMP,
            overlay="general-rnd",
            policy_binding_ref="policy:default",
        ),
        created_at=STAMP.isoformat(),
    )
    stores.governance.put_policy(
        ProjectPolicy(
            policy_id="policy:default",
            project_id=PROJECT,
            version=1,
            payload={},
            policy_digest=domain_digest(
                "PROJECT_POLICY", "1.0.0", canonical_payload({"project_id": PROJECT, "policy": {}})
            ),
            created_at=STAMP,
        )
    )
    clock, ids = SystemClock(), UuidIdGenerator()
    artifacts = create_resources(
        stores.ledger, stores, stores.projects, stores.governance, clock, ids
    )
    worker = FakeWorker()
    service = EmbeddingIndexService(
        store=stores.embedding_index,
        artifacts=artifacts,
        governance=stores.governance,
        projects=stores.projects,
        worker=worker,
        uow=stores.immediate_uow,
        clock=clock,
        ids=ids,
    )
    if enabled:
        service.configure(PROJECT, True, 0)
    return IndexFixture(stores, service, worker)
