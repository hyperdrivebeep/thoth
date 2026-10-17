"""Connect journal event preparation to the operation store's atomic claim boundary."""

from functools import partial

from thoth.domain.operation import OperationRecord
from thoth.ports.journal import JournalPort
from thoth.ports.operation import OperationStorePort


def claim_operation(
    operations: OperationStorePort, candidate: OperationRecord, journal: JournalPort | None
) -> tuple[OperationRecord, bool]:
    if journal is None:
        return operations.claim(candidate)
    return operations.claim(candidate, started_event=partial(journal.prepare_started, candidate))
