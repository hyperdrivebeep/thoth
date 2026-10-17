"""Safe CLI output for already-completed workspace authentication operations."""

import json

from rich.console import Console


def present_auth_status(console: Console, payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        safe = {
            key: payload.get(key)
            for key in (
                "provider",
                "connected",
                "connection_state",
                "profile_mode",
                "reason_code",
                "execution_eligible",
                "execution_verified",
            )
            if key in payload
        }
        console.print_json(json.dumps(safe))
    else:
        state = str(payload.get("connection_state") or "UNKNOWN")
        console.print(f"Codex account: {state}")


def present_auth_connect(console: Console, result: dict[str, object]) -> None:
    safe = {
        key: result.get(key)
        for key in (
            "started",
            "connected",
            "connection_state",
            "reason_code",
            "execution_eligible",
            "execution_verified",
            "guidance",
        )
        if key in result
    }
    console.print_json(json.dumps(safe))
