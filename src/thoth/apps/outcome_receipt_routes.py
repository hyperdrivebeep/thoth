"""Register the outcome and receipt RPCs."""

from thoth.application.commands import OutcomeHandlers, ReceiptHandlers
from thoth.protocol.registry import MethodRegistry


def register_outcome_and_receipt_methods(
    registry: MethodRegistry, outcome_handlers: OutcomeHandlers, receipt_handlers: ReceiptHandlers
) -> None:
    registry.register("outcome/list", outcome_handlers.list)
    registry.register("outcome/read", outcome_handlers.read)
    registry.register("outcome/series/list", outcome_handlers.series_list)
    registry.register("outcome/series/read", outcome_handlers.series_read)
    registry.register("outcome/profile/list", outcome_handlers.profile_list)
    registry.register("outcome/profile/read", outcome_handlers.profile_read)
    registry.register("outcome/attribution/read", outcome_handlers.attribution_read)
    registry.register("outcome/changeSet/read", outcome_handlers.change_set_read)
    registry.register("outcome/impact/read", outcome_handlers.impact_read)
    registry.register("outcome/audit/read", outcome_handlers.audit_read)
    registry.register("outcome/series/create", outcome_handlers.series_create)
    registry.register("outcome/observation/link", outcome_handlers.observation_link)
    registry.register("outcome/assess", outcome_handlers.assess)
    registry.register("outcome/reassess", outcome_handlers.reassess)
    registry.register("outcome/attribution/assess", outcome_handlers.attribution_assess)
    registry.register("outcome/changeSet/propose", outcome_handlers.change_set_propose)
    registry.register("outcome/followup/generate", outcome_handlers.followup_generate)
    registry.register("outcome/impact/propose", outcome_handlers.impact_propose)
    registry.register("receipt/list", receipt_handlers.list)
    registry.register("receipt/read", receipt_handlers.read)
    registry.register("receipt/stream/read", receipt_handlers.stream_read)
    registry.register("receipt/lineage/read", receipt_handlers.lineage_read)
    registry.register("receipt/bundle/read", receipt_handlers.bundle_read)
    registry.register("receipt/verification/read", receipt_handlers.verification_read)
    registry.register("receipt/audit/read", receipt_handlers.audit_read)
    registry.register("receipt/seal", receipt_handlers.seal)
    registry.register("receipt/verify", receipt_handlers.verify)
    registry.register("receipt/bundle/create", receipt_handlers.bundle_create)
    registry.register("receipt/bundle/verify", receipt_handlers.bundle_verify)
    registry.register("receipt/correction/create", receipt_handlers.correction_create)
