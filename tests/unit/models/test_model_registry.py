from __future__ import annotations

from typing import cast

import pytest

from thoth.adapters.models import RegisteredModelResolver
from thoth.ports.model import ModelPort, ModelResolutionError


def test_registered_model_resolver_accepts_extension_without_core_provider_branch() -> None:
    resolver = RegisteredModelResolver()
    model = cast(ModelPort, object())
    resolver.register("custom-provider", lambda model_name: model)

    assert resolver.resolve(provider="custom-provider", model="fixture") is model
    with pytest.raises(ModelResolutionError, match="not registered"):
        resolver.resolve(provider="missing-provider", model=None)
