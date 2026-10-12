"""Explicit old-client display projection. Canonical records are never downgraded or saved."""

from copy import deepcopy

from pydantic import JsonValue

from thoth.protocol.jsonrpc import RpcApplicationError, RpcErrorCode


def base_read_params(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    omitted = {
        "contract_version",
        "cursor",
        "refresh_account_quota",
        "view",
        "expected_result_contract",
    }
    return {k: v for k, v in value.items() if k not in omitted}


def expected_result_view(
    payload: dict[str, JsonValue], requested: JsonValue
) -> dict[str, JsonValue]:
    if requested == "v4":
        return payload
    if requested is not None:
        raise RpcApplicationError(RpcErrorCode.INVALID_PARAMS, "EXPECTED_RESULT_VIEW_UNSUPPORTED")
    projected = deepcopy(payload)
    changed = False
    for name in ("current_result", "previous_result"):
        manifest = projected.get(name)
        result = manifest.get("result") if isinstance(manifest, dict) else None
        if not isinstance(result, dict):
            continue
        portfolio = result.get("portfolio")
        hypotheses = portfolio.get("hypotheses") if isinstance(portfolio, dict) else None
        if isinstance(hypotheses, list):
            for hypothesis in hypotheses:
                tests = (
                    hypothesis.get("discriminating_tests") if isinstance(hypothesis, dict) else None
                )
                for test in tests if isinstance(tests, list) else ():
                    if isinstance(test, dict):
                        changed |= _legacy_rows(test.get("expected_by_hypothesis"))
        filling = result.get("hypothesis_table_fill")
        if isinstance(filling, dict):
            changed |= _legacy_rows(filling.get("answers"))
    if changed:
        projected["expected_result_view"] = "LEGACY_DISPLAY_PROJECTION"
    return projected


def _legacy_rows(value: JsonValue) -> bool:
    changed = False
    if isinstance(value, list):
        for row in value:
            if not isinstance(row, dict) or "knowledge_state" not in row:
                continue
            state = row.pop("knowledge_state")
            if state == "UNKNOWN":
                row["expected"] = "모름"
            elif state == "PREDICTED" and " ".join(str(row.get("expected", "")).split()) == "모름":
                # The old codec cannot represent this prediction without calling it unknown.
                # Refuse that lossy view; never rewrite the prediction to invent a distinction.
                raise RpcApplicationError(
                    RpcErrorCode.DOMAIN_REJECTED, "EXPECTED_RESULT_VIEW_REQUIRES_V4"
                )
            elif row.get("expected") is None:
                row["expected"] = ""
            changed = True
    return changed


def result_read_view(payload: dict[str, JsonValue], requested: JsonValue) -> dict[str, JsonValue]:
    """Project only the result DTO; an included canonical manifest stays byte-identical."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    projected = expected_result_view({"current_result": {"result": result}}, requested)
    if "expected_result_view" not in projected:
        return payload
    manifest = projected["current_result"]
    assert isinstance(manifest, dict)
    return {
        **payload,
        "result": manifest["result"],
        "expected_result_view": projected["expected_result_view"],
    }
