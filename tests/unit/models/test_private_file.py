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

# SYSTEM, Administrators and OWNER RIGHTS. An entry for one of them may stay on a private file: they
# are not other ordinary accounts, and an administrator can read it anyway. Which of them a new file
# starts with depends on the machine (a folder made by Python 3.13 with mode 0o700, the way pytest
# makes tmp_path, carries all three).
_SYSTEM, _ADMINISTRATORS, _OWNER_RIGHTS = "S-1-5-18", "S-1-5-32-544", "S-1-3-4"
_SYSTEM_SIDE = {_SYSTEM, _ADMINISTRATORS, _OWNER_RIGHTS}

# The saved form writes well-known accounts by a two-letter name instead of their SID.
_NAMED = {
    "SY": _SYSTEM,
    "BA": _ADMINISTRATORS,
    "OW": _OWNER_RIGHTS,
    "WD": "S-1-1-0",  # Everyone
    "AU": "S-1-5-11",  # Authenticated Users
    "BU": "S-1-5-32-545",  # Users
    "BG": "S-1-5-32-546",  # Guests
    "IU": "S-1-5-4",  # INTERACTIVE
    "NU": "S-1-5-2",  # NETWORK
    "AN": "S-1-5-7",  # ANONYMOUS LOGON
}
# Two accounts of the machine itself are also written by name. Their SID is the machine's own number
# followed by a fixed last part: 500 for the built-in Administrator, 501 for Guest.
_NAMED_ON_THIS_MACHINE = {"LA": "500", "LG": "501"}

Entry = tuple[str, str, str]


def _named_sid(trustee: str, user_sid: str) -> str:
    """The SID a saved entry names, so that an account is the same whichever way it was written.

    A hosted runner works as the built-in Administrator; its entry is saved as "LA", not as the SID
    that whoami reports. The machine's number is taken from the current user's SID, which is right
    when that user is an account of this machine. For a domain user "LA" then becomes a SID that is
    not theirs, and the entry counts as another account, which it is.
    """
    if trustee in _NAMED:
        return _NAMED[trustee]
    if trustee in _NAMED_ON_THIS_MACHINE:
        return f"{user_sid.rsplit('-', 1)[0]}-{_NAMED_ON_THIS_MACHINE[trustee]}"
    return trustee


def _entries(sddl: str, user_sid: str) -> tuple[str, list[Entry]]:
    """The control flags of a saved access list and (flags, rights, SID) for each of its entries."""
    control, _, rest = sddl.removeprefix("D:").partition("(")
    parts = [entry.split(";") for entry in ("(" + rest).strip("()").split(")(")]
    assert all(len(part) == 6 and part[0] == "A" for part in parts), sddl
    return control, [(part[1], part[2], _named_sid(part[5], user_sid)) for part in parts]


def _not_private(sddl: str, user_sid: str, *, inheritable: bool = False) -> list[str]:
    """Why this access list does not keep a secret from other ordinary accounts (empty: it does)."""
    control, entries = _entries(sddl, user_sid)
    problems: list[str] = []
    if "P" not in control:
        problems.append("later changes to the folder can flow in")
    if any("ID" in flags for flags, _, _ in entries):
        problems.append("an entry passed down by the folder is left")
    if ("OICI" if inheritable else "", "FA", user_sid) not in entries:
        problems.append("the current user does not have full control")
    others = {sid for _, _, sid in entries} - _SYSTEM_SIDE - {user_sid}
    if others:
        problems.append(f"another account or group is named: {sorted(others)}")
    return problems


def _icacls(*arguments: str) -> None:
    subprocess.run(["icacls", *arguments], capture_output=True, check=True)


def _saved_acl(path: Path) -> str:
    """The access list as icacls saves it. The saved form is the same in every display language, and
    it tells an inherited entry (flag ID) from one set on the file itself."""
    saved = path.with_name(path.name + ".saved-acl")
    _icacls(str(path), "/save", str(saved))
    sddl = saved.read_text(encoding="utf-16-le").splitlines()[1]
    saved.unlink()
    return sddl


def _acl(path: Path) -> tuple[str, list[Entry]]:
    return _entries(_saved_acl(path), current_user_sid())


def _assert_private(path: Path, *, inheritable: bool = False) -> None:
    sddl = _saved_acl(path)
    assert _not_private(sddl, current_user_sid(), inheritable=inheritable) == [], sddl


_USER = "S-1-5-21-1111111111-2222222222-3333333333-1001"
_BUILT_IN_ADMINISTRATOR = "S-1-5-21-3289802278-1561746829-1498425070-500"


def test_an_account_written_by_name_is_the_same_account_as_its_sid() -> None:
    # What a hosted Windows runner saved: the current user is the built-in Administrator, written
    # "LA", next to the three entries the temporary folder passes on.
    runner = "D:P(A;;FA;;;LA)(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;OW)"
    assert _not_private(runner, _BUILT_IN_ADMINISTRATOR) == []
    assert _entries(runner, _BUILT_IN_ADMINISTRATOR)[1] == [
        ("", "FA", _BUILT_IN_ADMINISTRATOR),
        ("", "FA", _SYSTEM),
        ("", "FA", _ADMINISTRATORS),
        ("", "FA", _OWNER_RIGHTS),
    ]
    # An ordinary account is saved by its SID.
    assert _not_private(f"D:PAI(A;;FA;;;{_USER})", _USER) == []
    assert _not_private(f"D:P(A;OICI;FA;;;{_USER})(A;OICI;FR;;;SY)", _USER, inheritable=True) == []


def test_a_saved_access_list_that_is_not_private_says_why() -> None:
    other_user = _USER[:-4] + "1002"
    for sddl, problem in (
        (
            f"D:P(A;;FA;;;{_USER})(A;;FR;;;BU)",
            "another account or group is named: ['S-1-5-32-545']",
        ),
        (f"D:P(A;;FA;;;{_USER})(A;;FR;;;WD)", "another account or group is named: ['S-1-1-0']"),
        (
            f"D:P(A;;FA;;;{_USER})(A;;FR;;;{other_user})",
            f"another account or group is named: ['{other_user}']",
        ),
        # The built-in Administrator is another account when the current user is someone else.
        (
            f"D:P(A;;FA;;;{_USER})(A;;FA;;;LA)",
            f"another account or group is named: ['{_USER[:-4]}500']",
        ),
        (f"D:P(A;;FA;;;{_USER})(A;ID;FA;;;SY)", "an entry passed down by the folder is left"),
        (f"D:AI(A;;FA;;;{_USER})", "later changes to the folder can flow in"),
        (f"D:P(A;;FR;;;{_USER})", "the current user does not have full control"),
        ("D:P(A;;FA;;;SY)(A;;FA;;;BA)", "the current user does not have full control"),
    ):
        assert _not_private(sddl, _USER) == [problem], sddl


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
    assert {sid for _, _, sid in _acl(secret)[1]} == {current_user_sid(), *_SYSTEM_SIDE}


@windows_only
def test_a_private_folder_loses_broad_groups_and_keeps_system_entries(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    for sid in ("S-1-5-32-545", "S-1-1-0", "S-1-5-18", "S-1-5-32-544"):
        _icacls(str(profile), "/grant", f"*{sid}:(OI)(CI)R")

    make_private(profile, directory=True)

    _assert_private(profile, inheritable=True)
    # SYSTEM and Administrators were named on the folder itself and are not taken away.
    assert {sid for _, _, sid in _acl(profile)[1]} >= {_SYSTEM, _ADMINISTRATORS}


@pytest.mark.skipif(os.name == "nt", reason="file modes are the POSIX form")
def test_the_key_file_has_mode_0600(tmp_path: Path) -> None:
    _register(tmp_path, "openai", "sk-first")
    assert _secrets(tmp_path).stat().st_mode & 0o777 == 0o600
