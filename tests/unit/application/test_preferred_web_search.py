from thoth.application.services.connector_service import public_web_search_attempts
from thoth.domain.connectors import ConnectorAccessRequest
from thoth.domain.policy import AuthoritativeExecutionPolicy


def _request(query: str) -> ConnectorAccessRequest:
    return ConnectorAccessRequest.model_validate(
        {
            "actor_id": "actor:test",
            "project_id": "project:test",
            "connector_id": "connector:web",
            "operation": "DISCOVER",
            "selector": {"mode": "SEARCH", "query": query},
            "policy_id": "policy:test",
            "policy_revision": 1,
            "policy_digest": "a" * 64,
        }
    )


def _policy(*, enabled: bool, hosts: tuple[str, ...]) -> AuthoritativeExecutionPolicy:
    return AuthoritativeExecutionPolicy.model_validate(
        {
            "policy_id": "policy:test",
            "project_id": "project:test",
            "policy_revision": 1,
            "policy_digest": "a" * 64,
            "connector_allowlist": ("connector:web",),
            "connector_allowed_egress_classes": ("ALLOWLISTED_EXTERNAL",),
            "max_source_security_class": "PUBLIC",
            "sandbox_runtime_allowlist": (),
            "sandbox_network_policy": "ALLOWLIST",
            "sandbox_allowed_hosts": hosts,
            "public_web_enabled": enabled,
            "preferred_hosts": hosts,
        }
    )


def test_preferred_hosts_run_before_open_search() -> None:
    attempts = public_web_search_attempts(
        _request("mobilenet table 6"),
        _policy(enabled=True, hosts=("arxiv.org", "openreview.net")),
    )
    assert len(attempts) == 2
    assert "site:arxiv.org" in str(attempts[0].selector["query"])
    assert attempts[1].selector["query"] == "mobilenet table 6"


def test_no_fallback_when_web_off() -> None:
    attempts = public_web_search_attempts(
        _request("mobilenet"),
        _policy(enabled=False, hosts=("arxiv.org",)),
    )
    assert attempts == (_request("mobilenet"),)
