"""Shared exact owner policy for research controls and execution reentry."""

from thoth.domain.auth import current_authenticated_actor
from thoth.domain.operation import OperationRecord
from thoth.ports.resource_scope import ResourceAccessPort
from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


class ResearchOperationAccess:
    def __init__(self, access: ResourceAccessPort) -> None:
        self.access = access

    def require_execution_owner(self, operation: OperationRecord) -> None:
        self.access.require_operation(operation)
        actor = current_authenticated_actor()
        if actor is None and operation.owner_actor_id is None:
            return
        if actor is None or (
            operation.owner_actor_id,
            operation.owner_session_id,
            operation.owner_role_assignment_id,
            operation.owner_data_scopes,
        ) != (
            actor.actor_id,
            actor.session_id,
            actor.role_assignment_id,
            tuple(sorted(actor.data_scopes)),
        ):
            raise RpcApplicationError(
                RpcErrorCode.AUTHORIZATION_DENIED, "AUTH_OPERATION_OWNER_DENIED"
            )
