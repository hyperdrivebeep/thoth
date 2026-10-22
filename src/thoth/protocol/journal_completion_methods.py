"""V-J measured durable result forms; unknown methods and forms are not repaired."""

from pydantic import JsonValue

_FORM_1 = frozenset(
    {
        "cutoff_at",
        "description",
        "lifecycle",
        "name",
        "overlay",
        "policy",
        "policy_binding_ref",
        "project_id",
        "public_web_execution",
        "references",
        "revision",
        "roles",
        "schema_version",
        "source_binding_ids",
        "source_bindings",
        "working_heads",
        "workstreams",
    }
)

_FORM_2 = frozenset(
    {
        "cutoff_at",
        "description",
        "impact",
        "lifecycle",
        "name",
        "overlay",
        "policy",
        "policy_binding_ref",
        "project_id",
        "public_web_execution",
        "references",
        "revision",
        "roles",
        "schema_version",
        "source_binding_ids",
        "source_bindings",
        "working_heads",
        "workstreams",
    }
)

_FORM_3 = frozenset(
    {
        "cutoff_at",
        "description",
        "lifecycle",
        "name",
        "overlay",
        "policy",
        "policy_binding_ref",
        "project_id",
        "public_web_execution",
        "reference",
        "references",
        "revision",
        "roles",
        "schema_version",
        "source_binding_ids",
        "source_bindings",
        "working_heads",
        "workstreams",
    }
)

_FORM_4 = frozenset(
    {
        "cutoff_at",
        "description",
        "lifecycle",
        "name",
        "overlay",
        "policy",
        "policy_binding_ref",
        "project_id",
        "public_web_execution",
        "references",
        "revision",
        "role",
        "roles",
        "schema_version",
        "source_binding_ids",
        "source_bindings",
        "working_heads",
        "workstreams",
    }
)

_FORM_5 = frozenset(
    {
        "baseline_candidate",
        "impact",
        "protected_authority_package",
    }
)

_FORM_6 = frozenset(
    {
        "branch",
        "content_mutated",
    }
)

_FORM_7 = frozenset(
    {
        "change_set",
        "conflict_state",
        "missing_dependencies",
    }
)

_FORM_8 = frozenset(
    {
        "change_set",
        "checks",
        "impact_propagation_plan",
        "state",
        "validation_bundle_digest",
    }
)

_FORM_9 = frozenset(
    {
        "change_set",
        "impact_propagation_plan",
        "state",
        "validation_bundle_digest",
    }
)

_FORM_10 = frozenset(
    {
        "conflict",
        "head_mutated",
        "merge_proposal",
        "receipt",
        "semantic_merge",
    }
)

_FORM_11 = frozenset(
    {
        "candidate_revision",
        "change_set",
        "head_mutated",
        "merge_proposal",
    }
)

_FORM_12 = frozenset(
    {
        "impact_preview",
        "proposal",
        "staged_snapshot",
        "validation_state",
    }
)

_FORM_13 = frozenset(
    {
        "recompute_task",
    }
)

_FORM_14 = frozenset(
    {
        "change_set",
        "external_effect_rollback_claimed",
        "impact_plan",
        "restore_proposal",
    }
)

SUPPORTED_RESULT_FORMS: dict[str, tuple[frozenset[str], ...]] = {
    "project/activate": (_FORM_1,),
    "project/archive": (_FORM_1,),
    "project/create": (_FORM_1,),
    "project/cutoff/update": (_FORM_2,),
    "project/metadata/update": (_FORM_1,),
    "project/overlay/update": (_FORM_1,),
    "project/policy/update": (_FORM_1,),
    "project/reference/import": (_FORM_3,),
    "project/restore": (_FORM_1,),
    "project/role/assign": (_FORM_4,),
    "project/role/revoke": (_FORM_4,),
    "revision/baseline/prepare": (_FORM_5,),
    "revision/branch/create": (_FORM_6,),
    "revision/changeSet/create": (_FORM_7,),
    "revision/changeSet/validate": (
        _FORM_8,
        _FORM_9,
    ),
    "revision/merge/propose": (_FORM_10,),
    "revision/merge/resolve": (_FORM_11,),
    "revision/propose": (_FORM_12,),
    "revision/recompute/request": (_FORM_13,),
    "revision/restore/propose": (_FORM_14,),
}
SUPPLEMENTABLE_METHODS = frozenset(SUPPORTED_RESULT_FORMS)


def supported_result(method: str, result: dict[str, JsonValue]) -> bool:
    return (
        frozenset(result) in SUPPORTED_RESULT_FORMS.get(method, ())
        and result.get("schema_version", "1.0.0") == "1.0.0"
    )
