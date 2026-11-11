"""Whether a project switched local retrieval on and its index is ready for every active source.

This is the one condition under which the research path uses dense scores for a project. It is read
for each request, so switching the project off, a source that changed after indexing, or a source
that is not indexed yet takes effect on the next question. Whether the scores can actually be used
for one question (the encoder is running, the answer comes in time) is decided later, per question.
"""

from collections.abc import Callable
from importlib.util import find_spec

from thoth.ports.embedding_index import EmbeddingIndexStorePort
from thoth.ports.governance import GovernanceStorePort

RUNTIME_MODULES = ("numpy", "onnxruntime", "tokenizers")


def runtime_dependencies_available() -> bool:
    """The optional local-retrieval extra is installed (checked without importing it)."""
    return all(find_spec(name) is not None for name in RUNTIME_MODULES)


class ProjectDenseOptIn:
    def __init__(
        self,
        *,
        store: EmbeddingIndexStorePort,
        governance: GovernanceStorePort,
        dependencies: Callable[[], bool] = runtime_dependencies_available,
    ) -> None:
        self._store, self._governance, self._dependencies = store, governance, dependencies

    def __call__(self, project: str) -> bool:
        config = self._store.configuration(project)
        if not config.enabled or not self._dependencies():
            return False
        active = {
            binding.artifact_id
            for binding in self._governance.list_source_bindings(project)
            if binding.state == "ACTIVE"
        }
        if not active:
            return False
        ready = {
            job.intent.artifact_id
            for job in self._store.jobs(project)
            if job.state == "READY"
            and job.intent.config_generation == config.generation
            and job.intent.model == config.model
            and job.intent.artifact_id in active
            and self._store.is_current_header(job.intent)
        }
        return active <= ready
