"""Who may call the local server, how its pages may be shown, and whose name a command carries."""

from __future__ import annotations

import importlib
from pathlib import Path

import httpx
import pytest

from thoth.adapters.http.app import LOCAL_ACTOR_ID, create_app
from thoth.protocol.jsonrpc import JsonRpcRequest

BASE = "http://127.0.0.1:8765"
http_app = importlib.import_module("thoth.adapters.http.app")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspace"
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("THOTH_WORKSPACE", str(root))
    monkeypatch.setenv("THOTH_WEB_DIST", str(tmp_path / "missing-dist"))
    monkeypatch.delenv("THOTH_ALLOWED_ORIGINS", raising=False)
    return root


def _rpc(method: str, payload: dict[str, object], key: str = "k") -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": key,
        "method": method,
        "params": {"_meta": {"idempotencyKey": key}, "input": payload},
    }


async def test_pages_of_the_local_server_may_not_be_framed(workspace: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url=BASE
    ) as c:
        response = await c.get("/healthz")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_a_refused_request_carries_the_headers_too(workspace: Path) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url=BASE
    ) as c:
        response = await c.post("/rpc/query", json={}, headers={"origin": "http://evil.example"})
    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"


async def test_the_hosted_review_site_keeps_its_frame_but_not_type_sniffing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_DEPLOYMENT_MODE", "HOSTED_REVIEW")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url=BASE
    ) as c:
        response = await c.get("/healthz")
    assert "content-security-policy" not in response.headers
    assert "x-frame-options" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    ("origin", "status"),
    (
        (BASE, 200),  # the page this server served
        ("http://localhost:5173", 200),  # the web development server
        ("http://localhost:3000", 403),  # another local web server
        ("http://127.0.0.1:8888", 403),  # a notebook
        ("http://localhost:8765", 403),  # the same port under another name is another origin
    ),
)
async def test_only_its_own_page_and_the_dev_server_may_call_the_server(
    workspace: Path, origin: str, status: int
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url=BASE
    ) as c:
        response = await c.post(
            "/rpc/query",
            json=_rpc("project/list", {"project_id": "system:projects"}),
            headers={"origin": origin},
        )
    assert response.status_code == status
    if status == 403:
        assert response.json()["code"] == "LOCAL_ORIGIN_REJECTED"
        assert not workspace.exists()


async def test_another_local_origin_is_let_in_only_when_the_operator_names_it(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THOTH_ALLOWED_ORIGINS", "http://localhost:3000")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url=BASE
    ) as c:
        named = await c.post(
            "/rpc/query",
            json=_rpc("project/list", {"project_id": "system:projects"}),
            headers={"origin": "http://localhost:3000"},
        )
        other = await c.post(
            "/rpc/query",
            json=_rpc("project/list", {"project_id": "system:projects"}, "k2"),
            headers={"origin": "http://localhost:3001"},
        )
    assert (named.status_code, other.status_code) == (200, 403)


def _request(method: str, actor: str) -> JsonRpcRequest:
    return JsonRpcRequest.model_validate(_rpc(method, {"project_id": "p", "actor_id": actor}))


@pytest.mark.parametrize(
    "method",
    (
        "project/archive",
        # names that look like reads but are commands: they record who acted
        "closure/readiness/assess",
        "action/preflight/read",
    ),
)
def test_a_command_cannot_carry_another_actors_name(method: str) -> None:
    denial = http_app._local_identity_denial(  # pyright: ignore[reportPrivateUsage]
        _request(method, "human:someone-else"), local_actor_id=LOCAL_ACTOR_ID
    )
    assert denial is not None and denial.error is not None
    assert denial.error.data == {
        "reason_code": "LOCAL_ACTOR_OVERRIDE_DENIED",
        "expected_actor_id": LOCAL_ACTOR_ID,
        "pre_io": True,
    }
    own = http_app._local_identity_denial(  # pyright: ignore[reportPrivateUsage]
        _request(method, LOCAL_ACTOR_ID), local_actor_id=LOCAL_ACTOR_ID
    )
    assert own is None


@pytest.mark.parametrize("method", ("project/list", "thread/read", "revision/history/read"))
def test_a_read_may_name_any_actor_as_a_filter(method: str) -> None:
    denial = http_app._local_identity_denial(  # pyright: ignore[reportPrivateUsage]
        _request(method, "human:someone-else"), local_actor_id=LOCAL_ACTOR_ID
    )
    assert denial is None


async def test_an_upload_for_an_unknown_project_is_refused_once_the_workspace_is_open(
    workspace: Path,
) -> None:
    app = create_app()
    files = {"file": ("note.md", b"# note\n", "text/markdown")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        # Before any request has opened the workspace nothing is known about its projects.
        early = await c.post(
            "/files/stage", files=files, headers={"x-thoth-project-id": "project:early"}
        )
        assert early.status_code == 200 and app.state.runtime is None

        created = await c.post(
            "/rpc",
            json=_rpc(
                "project/create",
                {
                    "project_id": "project:real",
                    "name": "real",
                    "cutoff_at": "2026-09-01T00:00:00Z",
                    "overlay": "general-rnd",
                },
                "create",
            ),
        )
        assert created.status_code == 200 and "error" not in created.json(), created.text
        inbox_before = sorted(
            path.name for path in (workspace / "inbox" / "web" / "projects").iterdir()
        )

        unknown = await c.post(
            "/files/stage", files=files, headers={"x-thoth-project-id": "project:typo"}
        )
        known = await c.post(
            "/files/stage", files=files, headers={"x-thoth-project-id": "project:real"}
        )

    assert unknown.status_code == 404 and unknown.json() == {"detail": "project was not found"}
    assert known.status_code == 200
    inbox_after = sorted(path.name for path in (workspace / "inbox" / "web" / "projects").iterdir())
    # Only the real project's folder was added: the refused upload wrote nothing.
    assert len(inbox_after) == len(inbox_before) + 1
