from __future__ import annotations

import pytest
from pydantic import JsonValue

from thoth.protocol.registry import FORBIDDEN_DIRECT_METHODS, PUBLIC_METHODS, MethodRegistry


def test_declared_surface_has_no_direct_authority_bypass() -> None:
    assert set(PUBLIC_METHODS).isdisjoint(FORBIDDEN_DIRECT_METHODS)


@pytest.mark.parametrize("method", sorted(FORBIDDEN_DIRECT_METHODS))
def test_registry_rejects_direct_authority_bypass(method: str) -> None:
    async def handler(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return value

    with pytest.raises(ValueError, match="prohibited"):
        MethodRegistry().register(method, handler)
