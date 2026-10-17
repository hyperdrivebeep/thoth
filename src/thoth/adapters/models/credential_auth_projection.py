"""Public authentication-method projections from already observed account state."""

from __future__ import annotations


def account_auth_methods(
    key: str,
    code: dict[str, object],
    oauth_status: dict[str, object],
    *,
    code_connected: bool,
    code_eligible: bool,
    code_tool_problem: bool,
    code_startable: bool,
    code_state: object,
    pkce_start: bool,
    pkce_connected: bool,
    pkce_eligible: bool,
    oauth: bool,
    xai_oauth: bool,
    codex_eligible: bool,
    xai_eligible: bool,
) -> list[dict[str, object]]:
    oauth_methods: list[dict[str, object]]
    if key == "anthropic":
        oauth_methods = [
            {
                "auth_method": "claude_code_login",
                "route": "claude-code",
                "connected": code_connected,
                "execution_eligible": code_eligible,
                "connection_state": (
                    code.get("reason_code", "CLAUDE_CODE_STATUS_UNAVAILABLE")
                    if code_tool_problem or not code
                    else code_state
                ),
                "reason_code": code.get("reason_code", "CLAUDE_CODE_STATUS_UNAVAILABLE"),
                "capabilities": {
                    "start": code_startable,
                    "status": True,
                    "cancel": True,
                    "manual_complete": True,
                },
            }
        ]
        if pkce_start:
            # The client-ID route stays hidden until an app registration enables it.
            oauth_methods.append(
                {
                    "auth_method": "claude_pkce",
                    "route": "claude-oauth",
                    "connected": pkce_connected,
                    "execution_eligible": pkce_eligible,
                    "connection_state": oauth_status.get(
                        "connection_state", oauth_status.get("auth_state", "LOGIN_REQUIRED")
                    ),
                    "reason_code": oauth_status.get("reason_code"),
                    "capabilities": {
                        "start": True,
                        "status": True,
                        "cancel": True,
                        "manual_complete": True,
                    },
                }
            )
    else:
        oauth_methods = [
            {
                "auth_method": "codex_isolated_browser" if key == "openai" else "xai_device_code",
                "route": "codex-oauth" if key == "openai" else "xai-oauth",
                "connected": oauth or xai_oauth,
                "execution_eligible": codex_eligible or xai_eligible,
                "connection_state": oauth_status.get(
                    "connection_state", oauth_status.get("auth_state", "LOGIN_REQUIRED")
                ),
                "reason_code": oauth_status.get("reason_code"),
                "capabilities": {
                    "start": True,
                    "status": True,
                    "cancel": True,
                    "manual_complete": False,
                },
            }
        ]
    return oauth_methods
