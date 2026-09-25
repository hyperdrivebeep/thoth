"""Small deterministic parser for the model settings commands."""

from pydantic import JsonValue

from thoth.domain.conversation import ConversationIntent, TuiSessionState


def parse_model_command(
    command: str, args: list[str], session: TuiSessionState
) -> tuple[ConversationIntent, str, dict[str, JsonValue]]:
    if session.active_project_id is None:
        raise ValueError("Select a project before changing model settings.")
    scope: dict[str, JsonValue] = {"project_id": session.active_project_id}
    if "--project" not in args and session.active_thread_id is not None:
        scope["thread_id"] = session.active_thread_id
    args = [arg for arg in args if arg != "--project"]
    if not args or args == ["list"]:
        return ConversationIntent.ASK_STATUS, "model/settings/read", scope
    if command == "/model" and args == ["reset"]:
        return ConversationIntent.RESET_MODEL, "model/settings/update", {**scope, "selection": {}}
    if command == "/reasoning" and len(args) == 1:
        return (
            ConversationIntent.SET_REASONING,
            "model/settings/update",
            {**scope, "selection": {"reasoning_effort": args[0]}},
        )
    if command == "/model" and len(args) in {2, 3}:
        selection: dict[str, JsonValue] = {"provider": args[0], "model": args[1]}
        if len(args) == 3:
            selection["reasoning_effort"] = args[2]
        return (
            ConversationIntent.SELECT_MODEL,
            "model/settings/update",
            {**scope, "selection": selection},
        )
    raise ValueError(
        "Use /model <provider> <model> [effort], /reasoning <effort>, or /model reset; "
        "--project saves the project default."
    )
