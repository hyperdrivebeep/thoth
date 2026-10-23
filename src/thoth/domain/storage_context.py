"""Pass the effective deployment mode through a synchronous storage-open scope."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode

_mode: ContextVar[DeploymentMode | None] = ContextVar("storage_deployment_mode", default=None)


def storage_deployment_mode() -> DeploymentMode:
    return _mode.get() or parse_deployment_mode()


@contextmanager
def storage_mode_scope(mode: DeploymentMode | None) -> Generator[None]:
    token = _mode.set(mode or storage_deployment_mode())
    try:
        yield
    finally:
        _mode.reset(token)
