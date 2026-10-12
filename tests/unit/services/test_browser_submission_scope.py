from thoth.application.services.browser_submission_scope import browser_submission_scope
from thoth.domain.auth import AuthenticatedActorContext, authenticated_actor_scope


def test_scope_is_opaque_and_different_workspaces_or_authenticated_sessions_do_not_share_it():
    first = browser_submission_scope("workspace:a")
    assert first == browser_submission_scope("workspace:a")
    assert first != browser_submission_scope("workspace:b")
    actor = AuthenticatedActorContext(
        actor_id="human:a",
        session_id="session:a",
        project_id="p",
        role_assignment_id="role:a",
        role="RESEARCHER",
        capabilities=(),
        data_scopes=(),
    )
    with authenticated_actor_scope(actor):
        a = browser_submission_scope("workspace:a")
    with authenticated_actor_scope(actor.model_copy(update={"session_id": "session:b"})):
        b = browser_submission_scope("workspace:a")
    assert a != b and a != first
    assert a.startswith("browser:") and len(a) == 40
    assert "human:a" not in a and "session:a" not in a and "workspace:a" not in a
