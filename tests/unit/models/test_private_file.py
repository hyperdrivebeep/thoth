"""The API key file: replaced as a whole, never written over what cannot be read, and private."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from thoth.adapters.models import private_file
from thoth.adapters.models.local_credentials import LocalCredentialHold, register_api_key
from thoth.adapters.models.private_file import (
    BROAD_GROUP_SIDS,
    UnreadableJsonError,
    make_private,
    read_for_replace,
    replace_json,
)
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


windows_only = pytest.mark.skipif(
    os.name != "nt", reason="the ACL is the Windows form of mode 0600"
)

# SYSTEM, Administrators and OWNER RIGHTS in the short names icacls writes. An entry for one of them
# may stay on a private file: they are not other ordinary accounts, and an administrator can read it
# anyway. Which of them a new file starts with depends on the machine (a folder made by Python 3.13
# with mode 0o700, the way pytest makes tmp_path, carries all three).
_SYSTEM_SIDE = {"SY", "BA", "OW"}


def _icacls(*arguments: str) -> None:
    subprocess.run(["icacls", *arguments], capture_output=True, check=True)


def _acl(path: Path) -> tuple[str, list[tuple[str, str, str]]]:
    """The access list as icacls saves it: its control flags and (flags, rights, trustee) per entry.

    The saved form is the same in every display language, and it tells an inherited entry (flag ID)
    from one set on the file itself.
    """
    saved = path.with_name(path.name + ".saved-acl")
    _icacls(str(path), "/save", str(saved))
    sddl = saved.read_text(encoding="utf-16-le").splitlines()[1]
    saved.unlink()
    control, _, rest = sddl.removeprefix("D:").partition("(")
    entries = [entry.split(";") for entry in ("(" + rest).strip("()").split(")(")]
    assert all(len(entry) == 6 and entry[0] == "A" for entry in entries), sddl
    return control, [(entry[1], entry[2], entry[5]) for entry in entries]


def _assert_private(path: Path, *, inheritable: bool = False) -> None:
    control, entries = _acl(path)
    # Later changes to the folder cannot flow in, and nothing the folder passed down is left.
    assert "P" in control
    assert [entry for entry in entries if "ID" in entry[0]] == []
    # The current user has full control.
    assert ("OICI" if inheritable else "", "FA", current_user_sid()) in entries
    # No other ordinary account or group is named: not Users, not Everyone, not anything else.
    assert {trustee for _, _, trustee in entries} - _SYSTEM_SIDE == {current_user_sid()}


@windows_only
def test_the_key_file_is_closed_to_other_windows_accounts(tmp_path: Path) -> None:
    _register(tmp_path, "openai", "sk-first")
    _assert_private(_secrets(tmp_path))


@windows_only
@pytest.mark.parametrize("group", BROAD_GROUP_SIDS)
def test_a_broad_group_named_on_the_file_itself_is_removed(tmp_path: Path, group: str) -> None:
    secret = tmp_path / "secret.json"
    secret.write_text("{}", encoding="utf-8")
    # Not inherited, so dropping the inherited entries alone would leave it.
    _icacls(str(secret), "/grant", f"*{group}:R")
    assert any("ID" not in flags and rights == "FR" for flags, rights, _ in _acl(secret)[1])

    make_private(secret)

    _assert_private(secret)


@windows_only
def test_entries_for_system_administrators_and_owner_rights_stay(tmp_path: Path) -> None:
    # What a hosted Windows runner showed: the three entries on the file itself, not inherited.
    secret = tmp_path / "secret.json"
    secret.write_text("{}", encoding="utf-8")
    for sid in ("S-1-5-18", "S-1-5-32-544", "S-1-3-4"):
        _icacls(str(secret), "/grant", f"*{sid}:F")

    make_private(secret)

    _assert_private(secret)
    assert {trustee for _, _, trustee in _acl(secret)[1]} == {current_user_sid(), "SY", "BA", "OW"}


@windows_only
def test_a_private_folder_loses_broad_groups_and_keeps_system_entries(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    for sid in ("S-1-5-32-545", "S-1-1-0", "S-1-5-18", "S-1-5-32-544"):
        _icacls(str(profile), "/grant", f"*{sid}:(OI)(CI)R")

    make_private(profile, directory=True)

    _assert_private(profile, inheritable=True)
    # SYSTEM and Administrators were named on the folder itself and are not taken away.
    assert {trustee for _, _, trustee in _acl(profile)[1]} >= {"SY", "BA"}


@pytest.mark.skipif(os.name == "nt", reason="file modes are the POSIX form")
def test_the_key_file_has_mode_0600(tmp_path: Path) -> None:
    _register(tmp_path, "openai", "sk-first")
    assert _secrets(tmp_path).stat().st_mode & 0o777 == 0o600
