"""G3 1a: the 3.0.0 single-lane policy keeps its serialization and digests when lanes are added.

The digests below were taken from the code as it stood before PROTECTED_EXACT was allowed as a
lane id. A stored 3.0.0 policy that names the original lane must stay the same bytes forever.
"""

from thoth.domain.behavior_policy import RetrievalLanePolicy
from thoth.domain.canonical import canonical_payload, domain_digest

DEFAULT_POLICY_DIGEST = "6bc942f1cef1a49bebf9007082d083f5c0d5cb70b8aa8b12dc4f2885e52fae07"
DEFAULT_CONTENT_DIGEST = "3bcf5036eba654b4280806311dd9675d1ece1bf746af651d858d6550302c4f2b"
SMALL_POLICY_DIGEST = "a2f2e7a5bef3c90a9e217c92774ec994b80db09d503b235e673420180d79153c"
SMALL_CONTENT_DIGEST = "c6977440e874460c7a1bf376536f8cc18ff6af5e96800f83a90d5aa204f4306d"


def policy_digest(policy: RetrievalLanePolicy) -> str:
    return domain_digest("RETRIEVAL_POLICY", "1.0.0", canonical_payload(policy))


def content_digest(policy: RetrievalLanePolicy) -> str:
    return domain_digest(
        "BEHAVIOR_ARTIFACT_CONTENT", "1.0.0", canonical_payload(policy.model_dump(mode="python"))
    )


def test_the_original_lane_policy_keeps_its_digests() -> None:
    default = RetrievalLanePolicy()
    small = RetrievalLanePolicy(max_spans=1, character_budget=100)
    assert policy_digest(default) == DEFAULT_POLICY_DIGEST
    assert content_digest(default) == DEFAULT_CONTENT_DIGEST
    assert policy_digest(small) == SMALL_POLICY_DIGEST
    assert content_digest(small) == SMALL_CONTENT_DIGEST


def test_the_original_lane_policy_keeps_its_serialized_form() -> None:
    assert RetrievalLanePolicy().model_dump(mode="json")["lanes"] == [
        {"lane_id": "LEXICAL_SUBSTRING_V1", "lane_version": "1.0.0"}
    ]
    explicit = RetrievalLanePolicy.model_validate(
        {"lanes": [{"lane_id": "LEXICAL_SUBSTRING_V1", "lane_version": "1.0.0"}]}
    )
    assert policy_digest(explicit) == DEFAULT_POLICY_DIGEST
