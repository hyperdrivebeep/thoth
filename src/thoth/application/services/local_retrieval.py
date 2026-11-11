"""Explicit local preparation; research policy activation is a separate authority."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from thoth.application.services.actor_authority import current_actor_for
from thoth.application.services.auto_index import AutoIndexer
from thoth.application.services.embedding_index_service import EmbeddingIndexService
from thoth.domain.deployment_mode import DeploymentMode
from thoth.domain.embedding_index import EmbeddingBudget, EmbeddingIndexConfig, EmbeddingIndexError
from thoth.domain.model_asset import (
    AssetImportBudget,
    EmbeddingAssetDescriptor,
    ModelAssetDownloadGrant,
    ModelAssetError,
    ModelAssetStatus,
)
from thoth.ports.artifact_ledger import ArtifactLedgerPort
from thoth.ports.auth import AuthSessionStorePort
from thoth.ports.embedding_index import EmbeddingIndexStorePort
from thoth.ports.governance import GovernanceStorePort
from thoth.ports.model_asset import ModelAssetDownloadPort, ModelAssetStorePort
from thoth.ports.project import ProjectStorePort
from thoth.ports.runtime import ClockPort

_JSON = TypeAdapter(dict[str, JsonValue])


class LocalRetrievalService:
    def __init__(
        self,
        *,
        mode: DeploymentMode,
        assets: ModelAssetStorePort,
        downloader: ModelAssetDownloadPort,
        store: EmbeddingIndexStorePort,
        projects: ProjectStorePort,
        artifacts: ArtifactLedgerPort,
        sessions: AuthSessionStorePort,
        governance: GovernanceStorePort,
        clock: ClockPort,
        dependency_available: Callable[[], bool],
        factory: Callable[[EmbeddingAssetDescriptor], EmbeddingIndexService],
    ) -> None:
        self.mode, self.assets, self.downloader, self.store = mode, assets, downloader, store
        self.projects, self.artifacts = projects, artifacts
        self.sessions, self.governance, self.clock = sessions, governance, clock
        self.dependency_available, self.factory = dependency_available, factory
        self.index: EmbeddingIndexService | None = None
        self._asset_active: dict[str, JsonValue] | None = None
        self._asset_task: asyncio.Task[object] | None = None
        self._index_task: asyncio.Task[object] | None = None
        self._index_project: str | None = None
        self._index_operation: str | None = None
        self._index_auto = False
        self._prepare_waiting = False
        self._closed = False
        self.auto = AutoIndexer(self)
        self._status_task: asyncio.Task[ModelAssetStatus] | None = None
        self._descriptor_task: asyncio.Task[EmbeddingAssetDescriptor] | None = None

    def authorize(self, project: str, *, write: bool, system: bool = False) -> str:
        if self._closed or self.mode is not DeploymentMode.LOCAL:
            raise ModelAssetError("LOCAL_RETRIEVAL_LOCAL_ONLY")
        if system != (project == "system:workspace"):
            raise ModelAssetError("LOCAL_RETRIEVAL_SCOPE_DENIED")
        actor = current_actor_for(project, self.sessions, self.governance, self.clock)
        if not system and self.projects.read(project) is None:
            raise ModelAssetError("LOCAL_RETRIEVAL_PROJECT_MISSING")
        if actor is not None:
            required = {"WRITE", "ADMIN"} if system and write else {"WRITE" if write else "READ"}
            if not required.issubset(actor.capabilities) or "PROJECT" not in actor.data_scopes:
                raise ModelAssetError("LOCAL_RETRIEVAL_SCOPE_DENIED")
        return "local:operator" if actor is None else actor.actor_id

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def is_local(self) -> bool:
        return self.mode is DeploymentMode.LOCAL

    @property
    def index_busy(self) -> bool:
        return self._index_task is not None

    @property
    def prepare_waiting(self) -> bool:
        return self._prepare_waiting

    def start_auto_index(self) -> None:
        self.auto.start()

    def kick_auto_index(self) -> None:
        self.auto.kick()

    def claim_index_slot(self, task: "asyncio.Task[object]", project: str, *, auto: bool) -> None:
        if self._index_task is not None:
            raise EmbeddingIndexError("EMBEDDING_INDEX_BUSY")
        self._index_task, self._index_project, self._index_operation = task, project, None
        self._index_auto = auto

    def release_index_slot(self, task: "asyncio.Task[object]") -> None:
        if self._index_task is task:
            self._index_task = None
            self._index_project = None
            self._index_operation = None
            self._index_auto = False

    async def reconcile_project(self, project: str) -> None:
        """Close what a crash left running and queue what is missing. Reads every span of the
        project, off the event loop, so a caller with a time budget starts it after this."""
        index = self.index
        if index is None:
            raise EmbeddingIndexError("EMBEDDING_NOT_PREPARED")
        index.recover(project)
        await index.areconcile(project)

    async def drain_project(
        self,
        project: str,
        deadline: float,
        *,
        stop: Callable[[], bool] | None = None,
        resumable: bool = False,
    ) -> None:
        """Run the queued jobs of one project, then drop vectors no job refers to any more."""
        index = self.index
        if index is None:
            raise EmbeddingIndexError("EMBEDDING_NOT_PREPARED")
        await index.drain(project, deadline=deadline, stop=stop, resumable=resumable)
        if stop is None or not stop():
            await asyncio.to_thread(self.store.collect_orphans, project)

    def bind_index(self, service: EmbeddingIndexService) -> None:
        if self.index is not None and self.index is not service:
            raise ModelAssetError("EMBEDDING_INDEX_BUSY")
        self.index = service

    async def read_asset(self) -> dict[str, JsonValue]:
        self.authorize("system:workspace", write=False, system=True)
        if self._status_task is None or self._status_task.done():
            deadline = asyncio.get_running_loop().time() + 15
            self._status_task = asyncio.create_task(
                asyncio.to_thread(self.assets.status, deadline=deadline)
            )
            self._status_task.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
        status = await asyncio.wait_for(asyncio.shield(self._status_task), 15)
        state, reason = status.state, status.reason_code
        self.authorize("system:workspace", write=False, system=True)
        can_manage = True
        try:
            self.authorize("system:workspace", write=True, system=True)
        except ModelAssetError:
            can_manage = False
        return _JSON.validate_python(
            {
                "schema_version": "1.0.0",
                "supported": True,
                "can_manage": can_manage,
                "model_name": self.assets.manifest.model_id,
                "manifest_digest": self.assets.manifest.digest,
                "total_bytes": self.assets.manifest.total_bytes,
                "asset_state": state,
                "reason_code": reason,
                "dependency_available": self.dependency_available(),
                "worker_loaded": bool(self.index and getattr(self.index.worker, "prepared", False)),
                "active": self._asset_active,
                "license": self.assets.manifest.license,
                "source_url": self.assets.manifest.source,
            }
        )

    def read_project(self, project: str) -> dict[str, JsonValue]:
        self.authorize(project, write=False)
        can_prepare = True
        actor: str | None = None
        try:
            actor = self.authorize(project, write=True)
        except ModelAssetError:
            can_prepare = False
        config = self.store.configuration(project)
        if can_prepare and config.enabled and config.actor_ref != actor:
            can_prepare = False
        allowed = {a.artifact_id for a in self.artifacts.list_artifacts(project)}
        active = {
            b.artifact_id
            for b in self.governance.list_source_bindings(project)
            if b.state == "ACTIVE" and b.artifact_id in allowed
        }
        jobs: list[dict[str, object]] = []
        older_index = False
        for job in self.store.jobs(project):
            if job.intent.artifact_id in active and job.intent.model != config.model:
                older_index = True  # made under an earlier preprocessing version
            if (
                job.intent.config_generation != config.generation
                or job.intent.artifact_id not in active
                or not self.store.is_current(job.intent)
            ):
                continue
            state, reason = job.state, job.reason
            if state == "READY":
                try:
                    if (
                        self.store.ready(
                            project, job.intent.artifact_id, job.intent.source_version_id
                        )
                        is None
                    ):
                        state, reason = "FAILED", "INDEX_STALE"
                except (EmbeddingIndexError, ValueError):
                    state, reason = "FAILED", "INDEX_INCOMPLETE"
            jobs.append(
                dict(job_id=job.job_id, generation=job.generation, state=state, reason_code=reason)
            )
        ready = sum(j["state"] == "READY" for j in jobs)
        failed = sum(j["state"] in ("FAILED", "CANCELLED") for j in jobs)
        running = self._index_task is not None and self._index_project == project
        state = (
            "CANCEL_REQUESTED"
            if running and not config.enabled
            else "OFF"
            if not config.enabled
            else "INDEXING"
            if running
            else "NO_SOURCES"
            if not active
            else "FAILED"
            if failed
            else "READY"
            if ready == len(active)
            else "INDEX_PENDING"
        )
        return _JSON.validate_python(
            dict(
                project_id=project,
                can_prepare=can_prepare,
                enabled=config.enabled,
                generation=config.generation,
                state=state,
                ready_sources=ready,
                total_sources=len(active),
                failed_sources=failed,
                pending_sources=max(0, len(active) - ready - failed) if config.enabled else 0,
                # Shown only until the sources are indexed again under the current version.
                outdated_index=config.enabled and older_index and ready < len(active),
                jobs=jobs,
                active_operation_id=self._index_operation if running else None,
            )
        )

    def configure(self, project: str, enabled: bool, generation: int) -> dict[str, JsonValue]:
        actor = self.authorize(project, write=True)
        current = self.store.configuration(project)
        if current.generation != generation:
            raise EmbeddingIndexError("EMBEDDING_CONFIG_CONFLICT")
        unchanged = current.enabled == enabled and current.actor_ref == actor
        if not unchanged:
            budget = self.index.worker.budget if self.index else EmbeddingBudget()
            self.store.configure(
                EmbeddingIndexConfig(
                    project_id=project, enabled=enabled, actor_ref=actor, budget=budget
                ),
                generation,
            )
        if not enabled and self._index_project == project and self._index_task is not None:
            self._index_task.cancel()
        if enabled:
            self.kick_auto_index()
        return {**self.read_project(project), "unchanged": unchanged}

    async def asset_action(
        self,
        *,
        operation_id: str,
        manifest_digest: str,
        source: str | None = None,
        download: bool = False,
    ) -> dict[str, JsonValue]:
        actor = self.authorize("system:workspace", write=True, system=True)
        if manifest_digest != self.assets.manifest.digest:
            raise ModelAssetError("MODEL_MANIFEST_CHANGED")
        if self._asset_task is not None:
            raise ModelAssetError("MODEL_ASSET_BUSY")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + AssetImportBudget().timeout_ms / 1000
        task = asyncio.current_task()
        self._asset_task = task
        self._asset_active = dict(
            operation_id=operation_id,
            state="DOWNLOADING" if download else "COPYING",
            transferred_bytes=0,
            total_bytes=self.assets.manifest.total_bytes,
        )

        def progress(state: str, count: int) -> None:
            def apply() -> None:
                if self._asset_task is task and self._asset_active is not None:
                    self._asset_active.update(state=state, transferred_bytes=count)

            loop.call_soon_threadsafe(apply)

        def authorize_install() -> None:
            if self.authorize("system:workspace", write=True, system=True) != actor:
                raise ModelAssetError("LOCAL_RETRIEVAL_SCOPE_DENIED")

        try:
            if download:
                grant = ModelAssetDownloadGrant(
                    manifest_digest=manifest_digest,
                    operation_id=operation_id,
                    actor_ref=actor,
                    approved=True,
                )
                status = await self.downloader.install(
                    grant, deadline=deadline, progress=progress, check_authority=authorize_install
                )
            elif source is not None:
                status = await self.assets.import_directory(
                    Path(source),
                    deadline=deadline,
                    progress=lambda count: progress("COPYING", count),
                    check_authority=authorize_install,
                )
            else:
                raise ModelAssetError("MODEL_SOURCE_REQUIRED")
            self.kick_auto_index()  # the model may just have become usable
            return _JSON.validate_python(status.model_dump(mode="json"))
        finally:
            self._asset_task = None
            self._asset_active = None

    async def prepare(
        self,
        project: str,
        operation_id: str,
        *,
        generation: int | None = None,
        job_id: str | None = None,
        job_generation: int | None = None,
    ) -> dict[str, JsonValue]:
        self.authorize(project, write=True)
        config = self.store.configuration(project)
        if generation is not None and generation != config.generation:
            raise EmbeddingIndexError("EMBEDDING_CONFIG_CONFLICT")
        if not config.enabled:
            raise EmbeddingIndexError("EMBEDDING_DISABLED")
        if self._index_task is not None and not self._index_auto:
            raise EmbeddingIndexError("EMBEDDING_INDEX_BUSY")
        if not self.dependency_available():
            raise EmbeddingIndexError("EMBEDDING_DEPENDENCY_UNAVAILABLE")
        deadline = asyncio.get_running_loop().time() + config.budget.job_timeout_ms / 1000
        if self._index_task is not None:
            # The background indexer is working: it stops after the job in hand, then this command
            # takes the slot. Nothing is cancelled, so no job is left in a cancelled state.
            self._prepare_waiting = True
            try:
                async with asyncio.timeout_at(deadline):
                    while self.index_busy and self._index_auto:
                        await asyncio.sleep(0.1)
            finally:
                self._prepare_waiting = False
            if self.index_busy:
                raise EmbeddingIndexError("EMBEDDING_INDEX_BUSY")
        self._index_task, self._index_project, self._index_operation = (
            asyncio.current_task(),
            project,
            operation_id,
        )
        self._index_auto = False
        try:
            async with asyncio.timeout_at(deadline):
                if self._descriptor_task is not None and not self._descriptor_task.done():
                    raise EmbeddingIndexError("EMBEDDING_INDEX_BUSY")
                self._descriptor_task = asyncio.create_task(
                    asyncio.to_thread(self.assets.descriptor, deadline=deadline)
                )
                self._descriptor_task.add_done_callback(
                    lambda done: None if done.cancelled() else done.exception()
                )
                descriptor = await asyncio.shield(self._descriptor_task)
                self.authorize(project, write=True)
                if self.store.configuration(project).generation != config.generation:
                    raise EmbeddingIndexError("EMBEDDING_CONFIG_CONFLICT")
                if self.index is None:
                    self.index = self.factory(descriptor)
                if job_id is not None:
                    job = next((j for j in self.store.jobs(project) if j.job_id == job_id), None)
                    if job is None or job_generation is None:
                        raise EmbeddingIndexError("EMBEDDING_JOB_NOT_FOUND")
                    for ref in job.intent.refs:
                        self.artifacts.read_evidence(ref.span_id)
                    self.index.retry(project, job_id, job_generation)
                    await self.index.run_one(project, job_id, deadline=deadline)
                else:
                    await self.reconcile_project(project)
                    await self.drain_project(project, deadline)
        finally:
            self._index_task = None
            self._index_project = None
            self._index_operation = None
        return self.read_project(project)

    def close(self) -> None:
        self._closed = True
        self.auto.stop()
        if self._asset_task is not None:
            self._asset_task.cancel()
        if self._index_task is not None:
            self._index_task.cancel()
        if self.index is not None:
            self.index.close()
