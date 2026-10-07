"""An action request draft made from one discriminating test of a hypothesis.

A person presses a button; no model is asked. The draft names the test it came from. Its risk
comes from the effect declaration and the ordinary rules (the action service classifies the
declaration), never from the tier the model wrote on the test: a declaration that says nothing
is the rule's own R3 / POLICY_UNDEFINED. The draft is only a request being prepared. It is not an
approval, and what it may do is decided by the roles the rules ask for.
"""

from __future__ import annotations

from thoth.domain.hypothesis_full import HypothesisRecord

PURPOSE = "HYPOTHESIS_DISCRIMINATION"
HYPOTHESIS_NOT_FOUND = "HYPOTHESIS_NOT_FOUND"
TEST_NOT_FOUND = "TEST_NOT_FOUND"


class DraftRefused(ValueError):
    """The hypothesis or the test is not there; nothing is written."""


def draft_request(
    record: HypothesisRecord, test_id: str, declaration: dict[str, object]
) -> dict[str, object]:
    """The parameters of action/create for a draft from this test (without the project id)."""
    details = record.generation_details
    tests = () if details is None else details.discriminating_test_candidates
    test = next((item for item in tests if item.test_id == test_id), None)
    if test is None:
        raise DraftRefused(TEST_NOT_FOUND)
    return {
        "object_id": record.object_id,
        "hypothesis_refs": [record.hypothesis_id],
        "primary_purpose": PURPOSE,
        "specification": {
            "description": test.procedure_candidate,
            "expected_observation_or_change": {
                "description": test.procedure_candidate,
                "if_hypothesis_holds": test.expected_if_true,
                "if_alternative_holds": test.expected_if_alternative,
                "status": "CANDIDATE",
            },
            # What the person declares, as written. Nothing here is filled in on their behalf.
            "effect_vector": dict(declaration),
            "effect_completeness_confirmed": declaration.get("effect_completeness_confirmed")
            is True,
        },
        # a field of the request (covered by the revision digest), not part of the specification
        "test_refs": [{"hypothesis_id": record.hypothesis_id, "test_id": test.test_id}],
        "evidence_refs": list(record.evidence_refs),
    }
