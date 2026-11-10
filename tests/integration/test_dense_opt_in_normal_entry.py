"""Through the normal request entry: a project that opted in runs under its own retrieval policy."""

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.integration.paired_evaluation_helpers import pair_harness
from tests.integration.scoped_runtime import fixture_scope_policy
from tests.integration.storage_coverage_helpers import request, value
from tests.integration.test_research_request_v2 import ControlledResearchModel
from tests.integration.test_retrieval_shadow_normal_entry import (
    BASELINE,
    CANDIDATE,
    CASES,
    model_inputs,
    research,
)

from thoth.application.services.dense_opt_in import ProjectDenseOptIn
from thoth.domain.behavior_artifact import BehaviorArtifactKind


def answering(value: bool) -> Callable[[ProjectDenseOptIn, str], bool]:
    def answer(_self: ProjectDenseOptIn, _project: str) -> bool:
        return value

    return answer


async def test_an_opted_in_project_is_served_by_its_own_policy_and_the_models_get_the_same_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_retrieval_shadow_normal_entry import SOURCE

    model = ControlledResearchModel()
    (tmp_path / "inbox").mkdir(exist_ok=True)
    (tmp_path / "inbox" / "records.html").write_text(SOURCE, encoding="utf-8")
    async with pair_harness(
        tmp_path,
        {},
        {},
        CASES,
        policy_pair=(BASELINE, CANDIDATE),
        component=BehaviorArtifactKind.RETRIEVAL_POLICY,
        target_component="RETRIEVAL_QUERY_ROUTING",
        executor_id="BEHAVIOR_COMPONENT_SUBPROCESS_V1",
        runtime_options={"model_resolver": model, "resource_scope_policy": fixture_scope_policy()},
    ) as h:
        value(
            await h.runtime.bus.dispatch(
                request(
                    "project/source/connect",
                    "source",
                    {
                        "project_id": h.project,
                        "relative_path": "records.html",
                        "media_type": "text/html",
                        "authority": "INFORMAL",
                        "cutoff_state": "ELIGIBLE",
                        "security_class": "INTERNAL",
                    },
                )
            )
        )
        plain = await research(h, "plain")
        wanted = model_inputs(model)
        model.calls.clear()
        active = [
            s
            for s in plain["behavior_execution"]["snapshots"]
            if s["component"] == "RETRIEVAL_POLICY"
        ]
        assert [s["origin"] for s in active] == ["BASELINE"]

        # The runtime built its own index check from its stores; here the project is switched on.
        monkeypatch.setattr(ProjectDenseOptIn, "__call__", answering(True))
        opted = await research(h, "opted")
        assert model_inputs(model) == wanted
        execution = opted["behavior_execution"]
        (snapshot,) = [s for s in execution["snapshots"] if s["component"] == "RETRIEVAL_POLICY"]
        assert snapshot["origin"] == "PROJECT_OPT_IN"
        assert snapshot["reason_code"] == "LOCAL_RETRIEVAL_ENABLED"
        assert snapshot["policy"]["version"] == "5.0.0"
        assert snapshot["policy"]["exposure"] == "PROJECT_OPT_IN"
        assert snapshot["policy"]["max_spans"] == active[0]["policy"]["max_spans"]
        uses = [u for u in execution["uses"] if u["snapshot_digest"] == snapshot["snapshot_digest"]]
        assert uses and all(u["operation"] == "EVIDENCE_SELECTION" for u in uses)
        assert (
            opted["retrieval_coverage"]["candidate_count"]
            == plain["retrieval_coverage"]["candidate_count"]
        )

        # Switched off again: the next request is the plain baseline.
        monkeypatch.setattr(ProjectDenseOptIn, "__call__", answering(False))
        model.calls.clear()
        off = await research(h, "off")
        assert model_inputs(model) == wanted
        origins = [
            s["origin"]
            for s in off["behavior_execution"]["snapshots"]
            if s["component"] == "RETRIEVAL_POLICY"
        ]
        assert origins == ["BASELINE"]
