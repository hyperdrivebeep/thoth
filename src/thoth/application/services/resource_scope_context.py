"""Typed request-local preparation; never a source of implicit ownership."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from thoth.domain.resource_scope import ResourceIntakeBasis, StagedResourceScope

_INTAKE: ContextVar[ResourceIntakeBasis | None] = ContextVar("resource_intake_basis", default=None)
_STAGED: ContextVar[StagedResourceScope | None] = ContextVar("resource_staged_scope", default=None)


def current_resource_intake() -> ResourceIntakeBasis | None:
    return _INTAKE.get()


def current_resource_stage() -> StagedResourceScope | None:
    return _STAGED.get()


@contextmanager
def resource_intake_scope(value: ResourceIntakeBasis | None) -> Generator[None]:
    token = _INTAKE.set(value)
    try:
        yield
    finally:
        _INTAKE.reset(token)


@contextmanager
def resource_stage_scope(value: StagedResourceScope | None) -> Generator[None]:
    token = _STAGED.set(value)
    try:
        yield
    finally:
        _STAGED.reset(token)
