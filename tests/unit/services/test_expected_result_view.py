from copy import deepcopy

import pytest

from thoth.application.services.expected_result_view import expected_result_view
from thoth.protocol.jsonrpc import RpcApplicationError


def payload():
    return {
        "current_result": {
            "result": {
                "portfolio": {
                    "hypotheses": [
                        {
                            "discriminating_tests": [
                                {
                                    "expected_by_hypothesis": [
                                        {
                                            "hypothesis_id": "h1",
                                            "knowledge_state": "UNKNOWN",
                                            "expected": None,
                                            "basis": ["span:1"],
                                        },
                                        {
                                            "hypothesis_id": "h2",
                                            "knowledge_state": "PREDICTED",
                                            "expected": "stays slow",
                                            "basis": [],
                                        },
                                    ]
                                },
                            ]
                        }
                    ]
                }
            }
        }
    }


def rows(value):
    return value["current_result"]["result"]["portfolio"]["hypotheses"][0]["discriminating_tests"][
        0
    ]["expected_by_hypothesis"]


def test_legacy_projection_preserves_canonical_payload_and_unknown_basis():
    original = payload()
    before = deepcopy(original)
    shown = expected_result_view(original, None)
    assert original == before
    assert rows(shown)[0] == {"hypothesis_id": "h1", "expected": "모름", "basis": ["span:1"]}
    assert rows(shown)[1]["expected"] == "stays slow"
    assert shown["expected_result_view"] == "LEGACY_DISPLAY_PROJECTION"
    assert expected_result_view(original, "v4") is original


def test_old_client_must_not_misclassify_a_literal_prediction_as_unknown():
    original = payload()
    rows(original)[1]["expected"] = "모름"
    with pytest.raises(RpcApplicationError, match="EXPECTED_RESULT_VIEW_REQUIRES_V4"):
        expected_result_view(original, None)
    assert rows(expected_result_view(original, "v4"))[1]["knowledge_state"] == "PREDICTED"
