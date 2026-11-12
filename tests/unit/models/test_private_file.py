"""The API key file: replaced as a whole, never written over what cannot be read, and private."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from thoth.adapters.models import private_file
from thoth.adapters.models.local_credentials import LocalCredentialHold, register_api_key
from thoth.adapters.models.private_file import UnreadableJsonError, read_for_replace, replace_json
from thoth.adapters.models.windows_user_sid import current_user_sid


def _register(root: Path, provider: str, key: str) -> None:
    register_api_key(
        provider=provider, model="m", api_key=key, base_url="https://api.example.com/v1", root=root
    )


def _secrets(root: Path) -> Path:
    return root / "model-registry" / "secrets.json"


def test_a_second_provider_keeps_the_first_key(tmp_path: Path) -> None:
    _register(tmp_path, "openai", "sk-first")
    _register(tmp_path, "anthropic", "sk-second")
    assert json.loads(_secrets(tmp_path).read_text(encoding="utf-8")) == {
        "anthropic": "sk-second",
        "openai": "sk-first",
    }


@pytest.mark.parametrize("damaged", (b'{"openai": "sk-fi', b"[]", b"", b"\xff\xfe not json"))
def test_a_key_file_that_cannot_be_read_is_not_overwritten(tmp_path: Path, damaged: bytes) -> None:
    _register(tmp_path, "openai", "sk-first")
    _secrets(tmp_path).write_bytes(damaged)
    index_before = (tmp_path / "model-registry" / "index.json").read_bytes()

    with pytest.raises(LocalCredentialHold, match="MODEL_CREDENTIAL_STORE_UNREADABLE"):
        _register(tmp_path, "anthropic", "sk-second")

    # Read as empty, the new key alone would have replaced the file and the first key with it.
    assert _secrets(tmp_path).read_bytes() == damaged
    assert (tmp_path / "model-registry" / "index.json").read_bytes() == index_before


def test_a_write_that_fails_midway_leaves_the_old_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path, "openai", "sk-first")
    before = _secrets(tmp_path).read_bytes()

    def disk_full(_source: object, _target: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(private_file.os, "replace", disk_full)
    with pytest.raises(LocalCredentialHold, match="MODEL_CREDENTIAL_STORE_UNWRITABLE"):
        _register(tmp_path, "anthropic", "sk-second")

    assert _secrets(tmp_path).read_bytes() == before
    leftovers = [item.name for item in (tmp_path / "model-registry").iterdir()]
    assert sorted(leftovers) == ["index.json", "secrets.json"]


def test_reading_for_a_replacement_tells_missing_from_unreadable(tmp_path: Path) -> None:
    assert read_for_replace(tmp_path / "absent.json") == {}
    good = tmp_path / "good.json"
    replace_json(good, {"a": 1}, private=False)
    assert read_for_replace(good) == {"a": 1}
    assert good.read_text(encoding="utf-8") == '{\n  "a": 1\n}\n'
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(UnreadableJsonError):
        read_for_replace(bad)


@pytest.mark.skipif(os.name != "nt", reason="the ACL is the Windows form of mode 0600")
def test_the_key_file_is_readable_by_the_current_windows_user_only(tmp_path: Path) -> None:
    _register(tmp_path, "openai", "sk-first")
    listing = subprocess.run(
        ["icacls", str(_secrets(tmp_path))],
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        check=True,
    ).stdout
    entries = [line.strip() for line in listing.splitlines() if ":(" in line]
    # One explicit entry, full control, nothing inherited from the folder (no "(I)" entries).
    assert len(entries) == 1 and entries[0].endswith(":(F)")
    assert "(I)" not in listing
    sid_listing = subprocess.run(
        ["icacls", str(_secrets(tmp_path)), "/findsid", f"*{current_user_sid()}"],
        capture_output=True,
        text=True,
        encoding="oem",
        errors="replace",
        check=False,
    ).stdout
    assert "secrets.json" in sid_listing


@pytest.mark.skipif(os.name == "nt", reason="file modes are the POSIX form")
def test_the_key_file_has_mode_0600(tmp_path: Path) -> None:
    _register(tmp_path, "openai", "sk-first")
    assert _secrets(tmp_path).stat().st_mode & 0o777 == 0o600
