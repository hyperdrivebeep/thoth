from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MethodType
from typing import cast

from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request

from thoth.adapters.connectors import ConnectorRegistry, GitReadConnector, LocalFileConnector
from thoth.adapters.connectors.common import utc_now
from thoth.adapters.connectors.project_public_web import ProjectPublicWebConnector
from thoth.application.commands.sources import SourceCommandHandlers
from thoth.application.services.connector_service import ConnectorService
from thoth.apps.runtime import create_runtime
from thoth.apps.runtime_types import AppRuntime, RuntimeOptions
from thoth.domain.enums import OperationState
from thoth.domain.web_acquisition import WebPage
from thoth.ports.model import ModelResolverPort
from thoth.protocol.jsonrpc import JsonRpcResponse
from thoth.protocol.registry import MethodRegistry


def record(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in cast(dict[object, object], value))
    return cast(dict[str, object], value)


def items(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def text(value: object) -> str:
    assert isinstance(value, str)
    return value


def integer(value: object) -> int:
    assert type(value) is int
    return value


def rpc_value(response: JsonRpcResponse) -> dict[str, object]:
    assert response.error is None, response.error
    assert response.result is not None
    return record(response.result["value"])

LISTING_URI = "https://arxiv.org/list/cs.LG/recent"
PAPER_URI = "https://arxiv.org/abs/2609.00001"
UNOBSERVED_URI = "https://arxiv.org/abs/9999.99999"
LISTING_HTML = (
    b"<html><body>"
    b'<a href="/abs/2609.00001">FlashAttention follow-up</a>'
    b'<a href="https://evil.example/x">off host</a>'
    b"</body></html>"
)
PAPER_HTML = (
    b"<html><body>"
    b"<h1>FlashAttention-2</h1>"
    b"<p>A100 reports 312 TFLOPS in the published table. "
    b"That published accelerator figure is not an RTX 5080 idle watt reading.</p>"
    b"<p>The paper does not claim the local GPU log idle snapshot.</p>"
    b"</body></html>"
)
GPU_LOG_HTML = (
    "<html><body><h1>local GPU log</h1>"
    "<p>RTX 5080 idle 12W 0 percent util. This is a local measurement, not Table 1.</p>"
    "</body></html>"
)


class RecordingPublicReader:
    def __init__(self, pages: dict[str, bytes] | None = None) -> None:
        self.uris: list[str] = []
        self.pages = pages or {LISTING_URI: LISTING_HTML, PAPER_URI: PAPER_HTML}

    async def read(self, uri: str, *, max_bytes: int, timeout: float) -> WebPage:
        del max_bytes, timeout
        self.uris.append(uri)
        if uri not in self.pages:
            raise AssertionError(f"unexpected public read: {uri}")
        return WebPage(
            requested_uri=uri,
            final_uri=uri,
            media_type="text/html",
            raw=self.pages[uri],
            retrieved_at=utc_now(),
        )


def connector_service(runtime: AppRuntime) -> ConnectorService:
    registry: object = vars(runtime.bus).get("_registry")
    assert isinstance(registry, MethodRegistry)
    bound = registry.resolve("project/source/connect")
    assert isinstance(bound, MethodType)
    owner = bound.__self__
    assert isinstance(owner, SourceCommandHandlers)
    service: object = vars(owner).get("_connectors")
    assert isinstance(service, ConnectorService), "connector service was not composed"
    return service


def public_web_registry(workspace: Path, reader: RecordingPublicReader) -> ConnectorRegistry:
    def hosts_for(_project_id: str) -> tuple[str, ...]:
        return ("arxiv.org",)

    return ConnectorRegistry(
        (
            LocalFileConnector(workspace / "inbox"),
            GitReadConnector(workspace),
            ProjectPublicWebConnector(hosts_for, reader_factory=lambda _hosts: reader),
        )
    )


def web_runtime(
    workspace: Path,
    reader: RecordingPublicReader,
    *,
    model_resolver: ModelResolverPort | None = None,
) -> AppRuntime:
    from thoth.adapters.storage.workspace_setup import write_setup
    from thoth.domain.workspace_setup import WorkspaceSetupState

    write_setup(WorkspaceSetupState(internet_consent="ALLOWED"), workspace)
    options: RuntimeOptions = {
        "connector_registry": public_web_registry(workspace, reader),
        "resource_scope_policy": fixture_scope_policy(),
    }
    if model_resolver is not None:
        options["model_resolver"] = model_resolver
    return create_runtime(workspace, **options)


async def create_web_project(runtime: AppRuntime, project_id: str = "p") -> dict[str, object]:
    created = rpc_value(
        await runtime.bus.dispatch(
            request(
                "project/create",
                f"create-{project_id}",
                {
                    "project_id": project_id,
                    "name": "Public web",
                    "cutoff_at": "2026-09-19T00:00:00Z",
                },
            )
        )
    )
    return rpc_value(
        await runtime.bus.dispatch(
            request(
                "project/policy/update",
                f"enable-{project_id}",
                {
                    "project_id": project_id,
                    "expected_revision": integer(created["revision"]),
                    "payload": {
                        "public_web": {
                            "enabled": True,
                            "preferred_hosts": ["arxiv.org"],
                        }
                    },
                },
            )
        )
    )


async def finished_result(
    runtime: AppRuntime, question: str = "Does the 5080 idle log match the paper table?"
) -> dict[str, object]:
    admitted = rpc_value(
        await runtime.bus.dispatch(
            request(
                "thread/start",
                "start",
                {"project_id": "p", "problem": question, "contract_version": 2},
            )
        )
    )
    await runtime.bus.drain()
    operation = runtime.bus.read_operation(text(admitted["operation_id"]))
    assert operation is not None and operation.state is OperationState.SUCCEEDED, operation
    status = rpc_value(
        await runtime.bus.dispatch(
            request(
                "thread/read",
                "status",
                {"project_id": "p", "thread_id": text(admitted["thread_id"])},
            )
        )
    )
    result = record(record(status["current_result"])["result"])
    record(result["discovery"])
    record(result["retrieval"])
    record(result["source_packet"])
    return result


async def connect_html(
    runtime: AppRuntime,
    workspace: Path,
    relative_path: str,
    html: str,
    *,
    project_id: str = "p",
    cutoff_state: str = "ELIGIBLE",
) -> None:
    inbox = workspace / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / relative_path).write_text(html, encoding="utf-8")
    rpc_value(
        await runtime.bus.dispatch(
            request(
                "project/source/connect",
                f"connect-{relative_path}",
                {
                    "project_id": project_id,
                    "relative_path": relative_path,
                    "media_type": "text/html",
                    "authority": "INFORMAL",
                    "cutoff_state": cutoff_state,
                    "security_class": "INTERNAL",
                },
            )
        )
    )


def cutoff_at() -> datetime:
    return datetime(2026, 9, 19, tzinfo=UTC)


def artifact_uris(listed: dict[str, object]) -> list[str]:
    return [text(record(item)["source_uri"]) for item in items(listed["artifacts"])]
