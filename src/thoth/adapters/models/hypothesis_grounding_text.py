"""Grounding instructions add no scientific validation or separate model review stage."""

GROUNDING_V5 = (
    " PREDICTION_GROUNDING_V1. Add grounding.schema_version=1.0.0, derivation_role and rationale "
    "to each row. OBSERVATION_RESTATEMENT proposes a restatement of an observed result with "
    "the same subject, conditions, time, units, counts and qualifiers; cite 1-5 supplied spans. "
    "Do not relabel a source's hypothesis, opinion or forecast as an observation. "
    "EVIDENCE_INFERENCE cites 1-5 supplied spans as premises, not proof of the conclusion or "
    "a future intervention. For those two roles, give one or two concise sentences in rationale, "
    "at most 200 characters, in the accepted response language. HYPOTHESIS_ONLY uses basis=[] "
    'and may use rationale="" because the hypothesis already states the assumption. '
    'UNASSESSED also uses basis=[] and may use rationale="". UNKNOWN rows require '
    "expected=null and derivation_role=UNASSESSED. PREDICTED with UNASSESSED is permitted. "
    "A link is a model proposal, never a reviewed support judgement. Do not output source "
    "versions, hashes, relation assessments, verification claims or server provenance. "
    "Never use counterevidence or context-only references as supporting basis. Preserve "
    "uncertainty; do not invent links merely to fill basis. Keep test_id unique within each "
    "owner and each target hypothesis_id unique within its test. Do not add model calls."
)
