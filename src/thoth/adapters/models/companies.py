"""THOTH-owned company accounts: ChatGPT, Claude, xAI."""

from __future__ import annotations

COMPANIES: dict[str, dict[str, str]] = {
    "openai": {
        "label": "ChatGPT",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4.1",
        "login": "codex",
        "console_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "label": "Claude",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-5",
        "login": "claude",
        "console_url": "https://console.anthropic.com/settings/keys",
    },
    "xai": {
        "label": "xAI",
        "provider": "xai",
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-4.6",
        "login": "xai",
        "console_url": "https://console.x.ai/",
    },
}


def company(provider: str) -> dict[str, str] | None:
    key = provider.strip().lower()
    if key in {"chatgpt", "codex-oauth"}:
        key = "openai"
    return COMPANIES.get(key)
