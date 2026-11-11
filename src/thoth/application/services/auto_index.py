"""Background indexing: every project with local retrieval on gets its index built without a click.

Local retrieval is on for every project unless a person switched it off. This service looks, on a
timer and when something changes (start, a model that just became usable, a project switched on
again) and otherwise every minute, for projects that are on and have a source without an index,
and indexes them one at a time. It shares the one indexing slot of LocalRetrievalService with the
explicit "prepare index" command: it never starts while that slot is taken, and an explicit command
that arrives while it works waits only for the job in hand. It does nothing when the optional
runtime or the model files are missing, and a project that is switched off is never touched.
"""

import asyncio
import contextlib
from typing import TYPE_CHECKING

from thoth.domain.embedding_index import EmbeddingIndexError
from thoth.domain.enums import ProjectLifecycle
from thoth.domain.model_asset import ModelAssetError

if TYPE_CHECKING:
    from thoth.application.services.local_retrieval import LocalRetrievalService

AUTO_INDEX_TICK_SECONDS = 60.0
AUTO_INDEX_RETRY_SECONDS = 300.0
INDEXED_LIFECYCLES = (ProjectLifecycle.DRAFT, ProjectLifecycle.ACTIVE)


class AutoIndexer:
    def __init__(self, service: "LocalRetrievalService") -> None:
        self._service = service
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._retry_at: dict[str, float] = {}

    @property
    def started(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start on the running event loop; once, and only for a local workspace."""
        service = self._service
        if self.started or service.closed or not service.is_local:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._wake = asyncio.Event()
        self._task = loop.create_task(self._loop())
        self._task.add_done_callback(lambda done: None if done.cancelled() else done.exception())

    def kick(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        wake = self._wake
        assert wake is not None
        while not self._service.closed:
            wake.clear()
            try:
                await self.run_pass()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # background work never fails a request; the next tick looks again
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), AUTO_INDEX_TICK_SECONDS)

    def projects_needing_index(self) -> list[str]:
        service = self._service
        found: list[str] = []
        now_ms = int(service.clock.now().timestamp() * 1000)
        for project in service.projects.list():
            if project.lifecycle not in INDEXED_LIFECYCLES:
                continue
            try:
                # Jobs a crash left RUNNING (or already marked as lost to an expired lease) wait
                # again, so that they are indexed without a click.
                service.store.resume_interrupted(project.project_id, now_ms)
                if service.store.needs_indexing(project.project_id):
                    found.append(project.project_id)
            except Exception:
                continue
        return found

    async def run_pass(self) -> None:
        """One look at every project; indexes the ones that need it, one after the other."""
        service = self._service
        if not service.dependency_available():
            return
        loop = asyncio.get_running_loop()
        for project in await asyncio.to_thread(self.projects_needing_index):
            if service.closed or service.prepare_waiting or service.index_busy:
                return
            if self._retry_at.get(project, 0.0) > loop.time():
                continue
            await self._index_one(project)

    async def _index_one(self, project: str) -> None:
        service = self._service
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(self.run_project(project))
        service.claim_index_slot(task, project, auto=True)
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            service.release_index_slot(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            self._retry_at.pop(project, None)
        elif isinstance(error, TimeoutError):
            self.kick()  # a long project goes on in the next pass; its finished jobs are kept
        elif not isinstance(error, ModelAssetError):
            self._retry_at[project] = loop.time() + AUTO_INDEX_RETRY_SECONDS

    async def run_project(self, project: str) -> None:
        service = self._service
        config = service.store.configuration(project)
        if not config.enabled:
            return
        loop = asyncio.get_running_loop()
        budget = config.budget.job_timeout_ms / 1000

        def stop() -> bool:
            return service.closed or service.prepare_waiting

        try:
            if service.index is None:
                bind_by = loop.time() + budget
                async with asyncio.timeout_at(bind_by):
                    descriptor = await asyncio.to_thread(
                        service.assets.descriptor, deadline=bind_by
                    )
                if service.index is None:
                    service.bind_index(service.factory(descriptor))
            # Reading every source to queue its jobs can take longer than the budget on a large
            # project; the budget is for running the jobs, so it starts after that.
            await service.reconcile_project(project)
            # The time limit of the pass: a job it interrupts is put back to PENDING, not failed.
            deadline = loop.time() + budget
            await service.drain_project(project, deadline, stop=stop, resumable=True)
        except EmbeddingIndexError as error:
            if str(error) in ("EMBEDDING_DISABLED", "EMBEDDING_PROJECT_MISSING"):
                return
            raise
