"""Existing system RPCs that do not require input.project_id."""

SYSTEM_PROJECT_METHODS = frozenset(
    {
        "workspace/setup/read",
        "workspace/setup/update",
        "workspace/ready",
        "model/credential/list",
        "model/credential/register",
        "model/credential/login/status",
        "model/credential/login/cancel",
        "model/credential/login/complete",
        "model/catalog/refresh",
        "model/tooling/install",
    }
)
