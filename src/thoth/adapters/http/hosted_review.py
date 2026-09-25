"""HOSTED_REVIEW HTTP guards. Browser never receives operator keys."""

from __future__ import annotations

import os

from fastapi import Request

from thoth.domain.deployment_mode import DeploymentMode, parse_deployment_mode

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
CREDENTIAL_METHODS = frozenset({"model/credential/list", "model/credential/register"})
DISPATCH_GATE_TRUTHY = frozenset({"1", "true", "yes", "on"})


def hosted_mode() -> bool:
    return parse_deployment_mode() is DeploymentMode.HOSTED_REVIEW


def hosted_dispatch_gate_enabled() -> bool:
    """R2 snapshot gate is for Cloudflare Containers. Free tunnel origin skips it."""
    return os.environ.get("HOSTED_REVIEW_DISPATCH_GATE", "").strip().lower() in DISPATCH_GATE_TRUTHY


def hosted_internal_authorized(request: Request) -> bool:
    if not hosted_mode():
        return False
    secret = os.environ.get("THOTH_REVIEW_SESSION_SECRET", "").strip()
    if secret and request.headers.get("x-thoth-review-internal") == secret:
        return True
    session = os.environ.get("THOTH_REVIEW_SESSION_ID", "").strip()
    if not session or request.headers.get("x-thoth-review-session") != session:
        return False
    client = request.client.host if request.client is not None else ""
    return client in LOOPBACK_HOSTS


def hosted_transport_authorized(request: Request) -> bool:
    if not hosted_mode():
        return True
    secret = os.environ.get("THOTH_REVIEW_SESSION_SECRET", "").strip()
    session = os.environ.get("THOTH_REVIEW_SESSION_ID", "").strip()
    header_session = request.headers.get("x-thoth-review-session", "").strip()
    secret_ok = bool(secret and request.headers.get("x-thoth-review-internal") == secret)
    if session:
        return header_session == session or secret_ok
    return bool(header_session and secret_ok)


def hosted_request_session(request: Request) -> str | None:
    if not hosted_mode():
        return None
    session = os.environ.get("THOTH_REVIEW_SESSION_ID", "").strip()
    if session:
        return session
    header_session = request.headers.get("x-thoth-review-session", "").strip()
    return header_session or None


def is_hosted_credential_method(method: str) -> bool:
    return method in CREDENTIAL_METHODS
