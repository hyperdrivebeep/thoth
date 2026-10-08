"""Capture CLI help and parse failures with a synthetic, non-opening workspace default."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from thoth.apps import workspace_paths

INVOCATIONS = (
    ("--help",),
    ("doctor", "--help"),
    ("connector-sandbox-doctor", "--help"),
    ("profile-check", "--help"),
    ("source-stage", "--help"),
    ("git-snapshot", "--help"),
    ("profile-check",),
    ("source-stage",),
    ("doctor", "--unknown-option"),
    ("unknown-command",),
)


def capture() -> dict[str, object]:
    with patch.object(
        workspace_paths, "default_workspace", return_value=Path("contract-workspace")
    ):
        import thoth.cli as cli

        registered: list[str] = []
        shared_defaults: dict[str, bool] = {}
        for info in cli.app.registered_commands:
            callback = info.callback
            assert callback is not None
            name = info.name or str(callback.__name__).replace("_", "-")
            registered.append(name)
            if name in {"doctor", "source-stage", "git-snapshot"}:
                shared_defaults[name] = (
                    inspect.signature(callback).parameters["workspace"].default
                    is cli.DEFAULT_WORKSPACE
                )
        outputs: dict[str, object] = {}
        runner = CliRunner()
        for args in INVOCATIONS:
            result = runner.invoke(cli.app, list(args), prog_name="thoth", color=False)
            outputs[" ".join(args)] = {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        return {"registered": registered, "shared_defaults": shared_defaults, "outputs": outputs}


if __name__ == "__main__":
    print(json.dumps(capture(), ensure_ascii=True, sort_keys=True))
