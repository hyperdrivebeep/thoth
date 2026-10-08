"""Register criterion and object RPCs against their existing handler instances."""

from thoth.application.commands import CriterionFullHandlers, DecisionObjectHandlers
from thoth.protocol.registry import MethodRegistry


def register_criterion_and_object_methods(
    registry: MethodRegistry,
    criterion_handlers: CriterionFullHandlers,
    object_handlers: DecisionObjectHandlers,
) -> None:
    registry.register("criteria/list", criterion_handlers.list)
    registry.register("criteria/read", criterion_handlers.read)
    registry.register("criteria/profile/list", criterion_handlers.profile_list)
    registry.register("criteria/profile/read", criterion_handlers.profile_read)
    registry.register("criteria/reference/list", criterion_handlers.reference_list)
    registry.register("criteria/reference/read", criterion_handlers.reference_read)
    registry.register("criteria/conflict/list", criterion_handlers.conflict_list)
    registry.register("criteria/conflict/read", criterion_handlers.conflict_read)
    registry.register("criteria/audit/read", criterion_handlers.audit_read)
    registry.register("criteria/compile", criterion_handlers.compile)
    registry.register("criteria/field/correct", criterion_handlers.field_correct)
    registry.register("criteria/profile/apply", criterion_handlers.profile_apply)
    registry.register("criteria/revalidate", criterion_handlers.revalidate)
    registry.register("criteria/recalculate", criterion_handlers.recalculate)
    registry.register("criteria/reference/generate", criterion_handlers.reference_generate)
    registry.register("criteria/change/propose", criterion_handlers.change_propose)
    registry.register("object/list", object_handlers.list)
    registry.register("object/read", object_handlers.read)
    registry.register("object/candidate/list", object_handlers.candidate_list)
    registry.register("object/candidate/read", object_handlers.candidate_read)
    registry.register("object/profile/list", object_handlers.profile_list)
    registry.register("object/profile/read", object_handlers.profile_read)
    registry.register("object/relation/list", object_handlers.relation_list)
    registry.register("object/impact/read", object_handlers.impact_read)
    registry.register("object/attention/list", object_handlers.attention_list)
    registry.register("object/audit/read", object_handlers.audit_read)
    registry.register("object/materialize", object_handlers.materialize)
    registry.register("object/frame/revise", object_handlers.frame_revise)
    registry.register("object/profile/apply", object_handlers.profile_apply)
    registry.register("object/facet/update", object_handlers.facet_update)
    registry.register("object/relation/add", object_handlers.relation_add)
    registry.register("object/relation/remove", object_handlers.relation_remove)
    registry.register("object/revalidate", object_handlers.revalidate)
    registry.register("object/work/replan", object_handlers.work_replan)
    registry.register("object/split/propose", object_handlers.split_propose)
    registry.register("object/merge/propose", object_handlers.merge_propose)
    registry.register("object/attention/acknowledge", object_handlers.attention_acknowledge)
    registry.register("object/followup/create", object_handlers.followup_create)
