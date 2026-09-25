from __future__ import annotations

import pytest
from scripts.git_command_contract import checkpoint_command


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "candidate"; git push other main',
        "git push origin main --force",
        "git push origin main:other",
        "git push origin main extra",
        "git push origin main && echo later",
        "git -c core.hooksPath=empty commit -m x",
        "git -C ../other push origin main",
        "git commit -am x",
        "git commit -m x README.md",
        "git commit --amend -m x",
        "git push origin main\ngit push other main",
        'git commit -m "$(mutate)"',
        "git commit -m x > receipt.txt",
        'powershell -Command "git push origin main"',
    ],
)
def test_changed_or_mixed_git_command_is_rejected(command: str) -> None:
    assert checkpoint_command(command)[0] == "invalid"


def test_exact_target_retains_case_and_commit_has_no_implicit_staging() -> None:
    assert checkpoint_command("git push -u Origin Feature/ABC") == ("push", "Origin", "Feature/ABC")
    assert checkpoint_command('git commit -m "Reviewed candidate"') == ("commit", None, None)
    assert checkpoint_command("git status --short") == (None, None, None)
