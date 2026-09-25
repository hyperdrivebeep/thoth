from pathlib import Path

from fastapi.testclient import TestClient

from thoth.adapters.http.app import create_app
from thoth.cli import desktop_window_command


def test_same_origin_serves_ui_and_healthz(tmp_path: Path, monkeypatch: object) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>THOTH UI</html>", encoding="utf-8")
    monkeypatch.setenv("THOTH_WEB_DIST", str(dist))  # type: ignore[union-attr]
    monkeypatch.setenv("THOTH_WORKSPACE", str(tmp_path / "ws"))  # type: ignore[union-attr]
    client = TestClient(create_app())
    assert client.get("/healthz").json()["status"] == "ok"
    assert "THOTH UI" in client.get("/").text


def test_desktop_window_uses_edge_app_mode(tmp_path: Path) -> None:
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"edge")
    command = desktop_window_command(
        "http://127.0.0.1:8765/",
        system="Windows",
        program_files=tmp_path,
        program_files_x86=tmp_path / "missing",
    )
    assert command == [str(edge), "--app=http://127.0.0.1:8765/"]


def test_desktop_window_skips_non_windows() -> None:
    assert (
        desktop_window_command(
            "http://127.0.0.1:8765/",
            system="Linux",
            program_files=Path("."),
            program_files_x86=Path("."),
        )
        is None
    )
