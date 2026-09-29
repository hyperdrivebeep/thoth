from typing import Literal

from pydantic import Field

from thoth.domain.base import DomainModel

InternetConsent = Literal["UNDECIDED", "DENIED", "ALLOWED"]


class WorkspaceSetupState(DomainModel):
    schema_version: int = 1
    revision: int = Field(ge=1, default=1)
    internet_consent: InternetConsent = "UNDECIDED"
    internet_grant_id: str | None = None
    # Runtime read status is deliberately excluded from the canonical JSON file.
    storage_status: Literal["PRESENT", "MISSING", "CORRUPT", "UNREADABLE"] = Field(
        default="PRESENT", exclude=True
    )


class LocalWorkspaceReady(DomainModel):
    """Additive LOCAL projection; record access remains per existing RPC authorization."""

    ready: bool
    setup: WorkspaceSetupState
    model_connected: bool
    setup_complete: bool
    workspace_readable: bool
    execution_ready: bool
    workspace_id: str = Field(pattern=r"^workspace:[0-9a-f]{32}$")


class PublicWebPolicy(DomainModel):
    enabled: bool = False
    preferred_hosts: tuple[str, ...] = ()
    workspace_grant_id: str | None = None
