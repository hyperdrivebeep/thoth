import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { PackRunResult } from "./types";
import { AnalysisWorkspace } from "./components/AnalysisWorkspace";
import { EvidenceRail } from "./components/EvidenceRail";
import { StatusBadge } from "./components/StatusBadge";

describe("THOTH UI contracts", () => {
  it("renders machine states as readable badges", () => {
    const html = renderToStaticMarkup(<StatusBadge value="EXPERT_INPUT_REQUIRED" />);

    expect(html).toContain("EXPERT INPUT REQUIRED");
    expect(html).toContain("badge-expert-input-required");
  });

  it("keeps analysis, revision, memory and trust views in one decision object", () => {
    const result = {
      pack_id: "pack:test",
      scripted_model: true,
      evidence_count: 1,
      selected_evidence_count: 1,
      evidence: [],
      selected_evidence: [],
      project: { project_id: "project:test", name: "Test", lifecycle: "ACTIVE" },
      thread: { thread_id: "thread:test", problem: "Blocked", current_object_ids: ["object:test"] },
      cycle: {
        assessment: {
          assessment_id: "assessment:test",
          derived_status: ["INCOMPARABLE"],
          missing_items: [],
          scope_identity: { status: "PASS", reason: "scope" },
          criterion_authority: { status: "PASS", reason: "criterion" },
          evidence_coverage: { status: "PASS", reason: "evidence" },
          comparability: { status: "FAIL", reason: "conditions" },
        },
        portfolio: { portfolio_id: "portfolio:test", hypotheses: [{hypothesis_id:"hypothesis:nullable",statement:"Conditional predictive candidate",primary_locus:null,uncertainty:"Not validated",support_evidence_refs:[],counterevidence_queries:["Check counterexamples"],predicted_observations:[],discriminating_tests:[]}] },
        action_plan: { plan_id: "plan:test", frontier: [], alternatives: [] },
        commit: {
          disposition: "FAST_FORWARD",
          committed_revision_ids: [],
          receipt: {
            receipt_id: "receipt:test",
            receipt_digest: "0".repeat(64),
            claim_scopes: [],
            integrity_state: "VALID",
            provenance_state: "VALID",
            semantic_truth_certified: false,
          },
        },
        memory_ids: [],
        model_ids: ["SCRIPTED_MODEL"],
        semantic_repair_attempted: false,
        outcome: null,
      },
    } satisfies PackRunResult;

    const html = renderToStaticMarkup(<AnalysisWorkspace result={result} />);

    expect(html).toContain("판단");
    expect(html).toContain("변경 기록");
    expect(html).toContain("프로젝트 기억");
    expect(html).toContain("기준·권한·영수증");
    expect(html).toContain("마감·내보내기");
    expect(html).toContain("Conditional predictive candidate");
    expect(html).toContain("미기록");
    expect(html).toContain("badge-unrecorded");
  });

  it("labels bounded evidence separately from total evidence", () => {
    const html = renderToStaticMarkup(<EvidenceRail evidence={[]} totalCount={42} />);

    expect(html).toContain("활성 근거 0개");
    expect(html).toContain("전체 42개 중 선택");
  });

  it("renders a null scientific classification as unrecorded, never a fabricated status",()=>{
    const html=renderToStaticMarkup(<StatusBadge value={null}/>);
    expect(html).toContain("미기록");expect(html).not.toContain("PASS");expect(html).not.toContain("UNKNOWN");
  });
});
