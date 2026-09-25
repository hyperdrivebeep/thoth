"""Explicit Project-owned fixture policy for pre-ownership regression scenarios."""

from pathlib import Path
from typing import Unpack

from thoth.apps.runtime import AppRuntime
from thoth.apps.runtime import create_runtime as runtime_factory
from thoth.apps.runtime_types import RuntimeOptions
from thoth.domain.resource_scope import ResourceScopePolicy, ResourceScopeTemplate


def fixture_scope_policy() -> ResourceScopePolicy:
    return ResourceScopePolicy(
        default=ResourceScopeTemplate(owner_kind="PROJECT", visibility="PROJECT_SHARED")
    )


def create_runtime(workspace: Path, **options: Unpack[RuntimeOptions]) -> AppRuntime:
    options.setdefault("resource_scope_policy", fixture_scope_policy())
    return runtime_factory(workspace, **options)
