"""A location assessment is a support-policy signal, never a storage-safety certificate."""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import AwareDatetime

from thoth.domain.base import DomainModel
from thoth.domain.ids import Sha256

LocationKind = Literal[
    "KNOWN_LOCAL", "KNOWN_SYNC", "REMOTE_SHARE", "UNSUPPORTED_BRIDGE", "REMOVABLE", "UNKNOWN"
]


class WorkspaceLocationConfirmation(DomainModel):
    schema_version: Literal[1] = 1
    workspace_id: str
    classification: Literal["UNKNOWN"] = "UNKNOWN"
    reason_digest: Sha256
    confirmed_at: AwareDatetime


@dataclass(frozen=True)
class WorkspaceLocation:
    kind: LocationKind
    reasons: tuple[str, ...]
    canonical_path: str
    workspace_id: str
    confirmed: bool = False

    @property
    def blocked(self) -> bool:
        return self.kind in {"KNOWN_SYNC", "REMOTE_SHARE", "UNSUPPORTED_BRIDGE"}

    @property
    def reason_digest(self) -> str:
        payload = json.dumps([self.kind, sorted(self.reasons)], separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class WorkspaceLocationError(RuntimeError):
    def __init__(self, location: WorkspaceLocation) -> None:
        self.location = location
        self.code = (
            "WORKSPACE_LOCATION_BLOCKED" if location.blocked else "WORKSPACE_LOCATION_UNCONFIRMED"
        )
        super().__init__(self.code)
