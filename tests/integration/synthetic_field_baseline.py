"""Six test-owned field cases with a real typed seal and no private baseline."""

import hashlib
from pathlib import Path

from thoth.application.services.field_execution_tools import validate_sealed_baseline
from thoth.domain.canonical import canonical_payload, domain_digest
from thoth.domain.field_measurement import SealedFieldBaseline


def synthetic_sealed_baseline(tmp_path: Path) -> SealedFieldBaseline:
    paths: dict[str, str] = {}
    digests: dict[str, str] = {}
    for index in range(6):
        case = tmp_path / f"case-{index}"
        case.mkdir()
        content = f"synthetic bounded field case {index}\n".encode()
        (case / "case.txt").write_bytes(content)
        case_id = f"case:synthetic:{index}"
        paths[case_id] = case.as_posix()
        digests[case_id] = hashlib.sha256(b"case.txt" + content).hexdigest()
    draft: dict[str, object] = {
        "baseline_id": "baseline:synthetic-six-cases",
        "version": "1.0.0",
        "case_digests": digests,
        "case_paths": paths,
        "sequence_matrix": ("ABC", "BCA", "CAB", "ACB", "CBA", "BAC"),
        "baseline_toolchain": (
            "A:synthetic-manual",
            "B:synthetic-shared-tool",
            "C:synthetic-thoth",
        ),
        "hard_zero_metrics": ("unauthorized_r3",),
        "timebox_seconds": 1800,
        "sealed_at": "2026-09-02T00:00:00Z",
        "external_results": "NOT_RUN",
        "baseline_digest": "0" * 64,
    }
    typed = SealedFieldBaseline.model_validate(draft)
    seal = domain_digest(
        "FIELD_SEALED_BASELINE",
        "1.0.0",
        canonical_payload(typed.model_dump(mode="python", exclude={"baseline_digest"})),
    )
    sealed = typed.model_copy(update={"baseline_digest": seal})
    return validate_sealed_baseline(sealed.model_dump(mode="python"), actual_case_digests=digests)
