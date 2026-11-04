"""Existing system RPCs that do not require input.project_id."""

# The target project is deleted by the command, so its own operation, events and idempotency
# record cannot live under that project: they are recorded under the workspace instead.
WORKSPACE_OPERATION_METHODS = frozenset({"project/permanentDelete"})

SYSTEM_PROJECT_METHODS = frozenset(
    {
        "model/localAsset/read",
        "model/localAsset/import",
        "model/localAsset/install",
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
