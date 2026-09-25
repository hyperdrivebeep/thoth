import httpx
import pytest

from thoth.adapters.models.codex_http import CodexHttpExecutor
from thoth.domain.model_dispatch import OAuthSession
from thoth.ports.model import ModelTransportHold


class Session:
    def read(self) -> OAuthSession:
        return OAuthSession("fixture-token", "fixture-account", "fixture-model", "high")


@pytest.mark.parametrize(
    ("error_type", "phase"),
    [
        (httpx.ConnectTimeout, "CONNECT"),
        (httpx.ReadTimeout, "READ_IDLE"),
        (httpx.WriteTimeout, "WRITE"),
        (httpx.PoolTimeout, "POOL"),
    ],
)
async def test_transport_timeouts_keep_their_actual_cause(
    error_type: type[httpx.TimeoutException], phase: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("controlled phase deadline", request=request)

    executor = CodexHttpExecutor(Session(), transport=httpx.MockTransport(handler))
    prepared = executor.prepare(
        "fixture", {"type": "object"}, output_tokens=100, timeout_seconds=12
    )
    with pytest.raises(
        ModelTransportHold, match=f"OAUTH_{phase}_TIMEOUT_REMOTE_STOP_UNKNOWN"
    ) as failure:
        await executor.dispatch(prepared)
    assert failure.value.observation.timeout_kind == phase
    assert failure.value.observation.http_status is None
