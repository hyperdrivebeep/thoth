"""Opaque convenience-storage scope; it grants no execution or read authority."""

from thoth.domain.auth import current_authenticated_actor
from thoth.domain.canonical import canonical_payload, domain_digest


def browser_submission_scope(workspace_id: str) -> str:
    actor = current_authenticated_actor()
    return (
        "browser:"
        + domain_digest(
            "BROWSER_SUBMISSION_SCOPE",
            "1.0.0",
            canonical_payload(
                {
                    "workspace": workspace_id,
                    "actor": None if actor is None else actor.actor_id,
                    "session": None if actor is None else actor.session_id,
                }
            ),
        )[:32]
    )
