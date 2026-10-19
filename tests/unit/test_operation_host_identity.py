"""Synthetic OS identities: raw values never leave the supplier as tokens."""

import hashlib
import hmac
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from thoth.adapters import operation_host_identity as identity
from thoth.domain.deployment_mode import DeploymentMode

MACHINE = "12345678-1234-1234-1234-123456789abc"
BOOT = "87654321-4321-4321-4321-cba987654321"


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_cache", None)


def registry(
    monkeypatch: pytest.MonkeyPatch,
    boots: tuple[object, object] = (12, 12),
    *,
    machine: object = MACHINE,
    kind: int = 1,
):
    values = iter(boots)
    calls: list[str] = []

    def opened(root: int, path: str, reserved: int, access: int):
        assert root == 100 and reserved == 0 and access == (2 | 4)
        assert path in {
            r"SOFTWARE\Microsoft\Cryptography",
            (
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
                r"\PrefetchParameters"
            ),
        }
        return nullcontext(path)

    def query(key: str, name: str):
        del key
        calls.append(name)
        return (next(values), 3) if name == "BootId" else (machine, kind)

    monkeypatch.setitem(
        sys.modules,
        "winreg",
        SimpleNamespace(
            HKEY_LOCAL_MACHINE=100,
            KEY_READ=2,
            KEY_WOW64_64KEY=4,
            REG_DWORD=3,
            REG_SZ=1,
            OpenKey=opened,
            QueryValueEx=query,
        ),
    )
    monkeypatch.setattr(identity.sys, "platform", "win32")
    return calls


def test_windows_double_read_and_hash_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = registry(monkeypatch)
    token = identity.operation_host_token(DeploymentMode.LOCAL)
    expected = hmac.new(
        b"thoth.operation-owner.host.v1",
        ("win32\0" + MACHINE.replace("-", "") + "\0prefetch-bootid:12").encode(),
        hashlib.sha256,
    ).hexdigest()
    assert token is not None and token == expected and len(token) == 64
    assert identity.operation_host_token(DeploymentMode.LOCAL) == token
    assert calls == ["BootId", "MachineGuid", "BootId"]
    assert vars(identity)["_cache"] == (identity.os.getpid(), token)
    assert MACHINE not in repr(vars(identity)["_cache"])
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "boots,machine,kind",
    [
        ((12, 13), MACHINE, 1),
        ((-1, -1), MACHINE, 1),
        ((2**32, 2**32), MACHINE, 1),
        ((True, True), MACHINE, 1),
        ((12, 12), "bad-id", 1),
        ((12, 12), "00000000-0000-0000-0000-000000000000", 1),
        ((12, 12), MACHINE, 3),
    ],
)
def test_invalid_registry_or_changed_boot_caches_none(
    monkeypatch: pytest.MonkeyPatch, boots: tuple[object, object], machine: object, kind: int
) -> None:
    calls = registry(monkeypatch, boots, machine=machine, kind=kind)
    assert identity.operation_host_token(DeploymentMode.LOCAL) is None
    count = len(calls)
    assert identity.operation_host_token(DeploymentMode.LOCAL) is None
    assert len(calls) == count


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError, OSError])
def test_registry_failure_returns_none_without_exposing_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: type[OSError]
) -> None:
    registry(monkeypatch)

    def fail(*args: object):
        raise error(MACHINE)

    monkeypatch.setattr(sys.modules["winreg"], "OpenKey", fail)
    assert identity.operation_host_token(DeploymentMode.LOCAL) is None
    assert capsys.readouterr() == ("", "")


def test_zero_counter_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    registry(monkeypatch, (0, 0))
    assert identity.operation_host_token(DeploymentMode.LOCAL) is not None


def test_hosted_never_reads_even_when_cache_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = registry(monkeypatch)
    assert identity.operation_host_token(DeploymentMode.HOSTED_REVIEW) is None
    assert calls == [] and vars(identity)["_cache"] is None
    assert identity.operation_host_token(DeploymentMode.LOCAL) is not None
    assert identity.operation_host_token(DeploymentMode.HOSTED_REVIEW) is None
    assert len(calls) == 3


def test_linux_reads_bounded_files_and_uses_os_specific_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    machine, boot = tmp_path / "machine", tmp_path / "boot"
    machine.write_text(MACHINE.replace("-", "") + "\n")
    boot.write_text(BOOT + "\n")
    real_open = Path.open

    def opened(path: Path, *args: object, **kwargs: object):
        target = {"/etc/machine-id": machine, "/proc/sys/kernel/random/boot_id": boot}[
            path.as_posix()
        ]
        return real_open(target, "rb")

    monkeypatch.setattr(identity.sys, "platform", "linux")
    monkeypatch.setattr(Path, "open", opened)
    token = identity.operation_host_token(DeploymentMode.LOCAL)
    expected = hmac.new(
        b"thoth.operation-owner.host.v1",
        ("linux\0" + MACHINE.replace("-", "") + "\0" + BOOT.replace("-", "")).encode(),
        hashlib.sha256,
    ).hexdigest()
    assert token == expected


@pytest.mark.parametrize("raw", [b"", b"0" * 32, b"x" * 129, b"\xff" * 32])
def test_linux_invalid_or_oversize_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    import io

    monkeypatch.setattr(identity.sys, "platform", "linux")

    def opened(*args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(raw)

    monkeypatch.setattr(Path, "open", opened)
    assert identity.operation_host_token(DeploymentMode.LOCAL) is None


def test_concurrent_first_calls_read_once_and_new_pid_gets_new_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def read() -> str:
        calls.append(1)
        return "a" * 64

    monkeypatch.setattr(identity, "_read_token", read)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert (
            list(pool.map(identity.operation_host_token, [DeploymentMode.LOCAL] * 8))
            == ["a" * 64] * 8
        )
    assert calls == [1]
    monkeypatch.setattr(identity.os, "getpid", lambda: 123456789)
    assert identity.operation_host_token(DeploymentMode.LOCAL) == "a" * 64
    assert calls == [1, 1]
    old_lock = vars(identity)["_lock"]
    vars(identity)["_after_fork"]()
    assert vars(identity)["_cache"] is None and vars(identity)["_lock"] is not old_lock
    assert isinstance(vars(identity)["_lock"], type(threading.Lock()))


def test_unsupported_os_does_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity.sys, "platform", "unsupported")
    assert identity.operation_host_token(DeploymentMode.LOCAL) is None
