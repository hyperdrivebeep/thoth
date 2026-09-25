from __future__ import annotations

from thoth.domain.baseline import BaselineScope

_PREFIX_SCOPE = {
    "PROJECT": BaselineScope.MISSION_PRODUCT,
    "DECISION_OBJECT": BaselineScope.MISSION_PRODUCT,
    "HYPOTHESIS": BaselineScope.RESEARCH_HYPOTHESIS,
    "ACTION": BaselineScope.CODE_INFRASTRUCTURE,
    "EXECUTION": BaselineScope.CODE_INFRASTRUCTURE,
    "EVIDENCE": BaselineScope.EVALUATION_EVIDENCE,
    "CRITERION": BaselineScope.EVALUATION_EVIDENCE,
    "OUTCOME": BaselineScope.EVALUATION_EVIDENCE,
}


class BaselineRouter:
    def route(self, heads: dict[str, str]) -> dict[BaselineScope, dict[str, str]]:
        values: dict[BaselineScope, dict[str, str]] = {}
        for key, digest in sorted(heads.items()):
            scope = _PREFIX_SCOPE.get(key.split(":", 1)[0])
            if scope is not None:
                values.setdefault(scope, {})[key] = digest
        return values
