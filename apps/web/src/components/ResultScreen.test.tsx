import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import type { CoverageMatrixRow } from "../api/researchFollowup";
import type { ResearchStatus } from "../api/research";
import { ResearchResultCard } from "./ResearchResultCard";
import { classifyEvidenceUse, groupEvidenceUse } from "./evidenceUse";
import { nextActionLabel } from "./statusLabels";
import { resultUsageLine } from "./resultUsage";

const digest = "a".repeat(64);
type Row = CoverageMatrixRow;
const row = (id: string, relation: string, validation: string, extra: Partial<Row> = {}): Row => ({
  requirement_id: id, target: id.startsWith("bound") ? "time" : "RESEARCH_GAP", question: "질문 " + id, status: "UNRESOLVED", applicability: "APPLICABLE",
  relation, validation, blocker: "RESEARCH_GAP", review_refs: [], evidence_refs: [], reason_codes: [], ...extra });
/** The seven criteria of the stored IRIS run2 result (values copied from its coverage assessments). */
const iris: Row[] = [
  row("bound:0", "QUALIFIES", "INCONCLUSIVE", { blocker: "answer:HOLD" }),
  row("bound:1", "QUALIFIES", "INCONCLUSIVE", { blocker: "answer:HOLD" }),
  row("bound:2", "QUALIFIES", "INCONCLUSIVE", { blocker: "time:HOLD" }),
  row("check:0", "QUALIFIES", "APPLIED"),
  row("check:1", "QUALIFIES", "INCONCLUSIVE"),
  row("check:2", "INSUFFICIENT", "APPLIED"),
  row("check:3", "SUPPORTS", "APPLIED"),
];
const coverage = (rows: Row[]) => ({ schema_version: "1.0.0" as const, request_revision_digest: digest, availability: "AVAILABLE" as const,
  reason_codes: [], requirement_set_revision_digest: null, coverage_revision_digest: null, rows,
  summary: { satisfied: 0, unresolved: rows.length, not_applicable: 0, not_assessed: 0, hold_targets: [], reason_codes: [] } });
const action = (action_type: "REVIEW_GAPS" | "OPEN_RESULT_DETAIL" | "NONE", label: string, reason_codes: string[] = []) =>
  ({ schema_version: "1.0.0" as const, action_type, label, reason_codes, requires_permission: false, target: null, basis: {} });
const progress = (next: ReturnType<typeof action>) => ({ schema_version: "1.0.0" as const, request_revision_digest: digest, result_revision_digest: digest, state: "HOLD" as const,
  currentness: { state: "CURRENT" as const, reasons: [], execution_eligible: true }, progress_items: [], recorded_checks: [], remaining_gaps: [], unknowns: [], next_user_action: next });
const render = (result: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={result} onDetail={() => {}} {...extra}/>);
const held = { ...excerpt, answer_status: "PARTIAL_HOLD" } as Record<string, unknown>;

it("sorts the seven stored IRIS criteria into direct 1, conditional 5, insufficient 1 with nothing left unclassified", () => {
  const groups = groupEvidenceUse(iris);
  expect(groups.DIRECT.map(item => item.requirement_id)).toEqual(["check:3"]);
  expect(groups.CONDITIONAL).toHaveLength(5);
  expect(groups.INSUFFICIENT.map(item => item.requirement_id)).toEqual(["check:2"]);
  expect(groups.UNUSABLE).toHaveLength(0);
  expect(groups.UNCLASSIFIED).toHaveLength(0);
});

it("counts a value no rule covers as unclassified instead of guessing", () => {
  expect(classifyEvidenceUse(row("x", "SUPPORTS", "NOT_ASSESSED"))).toBe("UNCLASSIFIED");
  expect(classifyEvidenceUse(row("x", "SOMETHING_NEW", "APPLIED"))).toBe("UNCLASSIFIED");
  expect(classifyEvidenceUse(row("x", "IRRELEVANT", "APPLIED"))).toBe("UNUSABLE");
  expect(classifyEvidenceUse(row("x", "SUPPORTS", "REJECTED"))).toBe("UNUSABLE");
  expect(classifyEvidenceUse(row("x", "SUPPORTS", "APPLIED", { applicability: "NOT_APPLICABLE_CANDIDATE" }))).toBe("UNUSABLE");
  expect(classifyEvidenceUse(row("x", "REFUTES", "APPLIED"))).toBe("DIRECT");
  expect(classifyEvidenceUse(row("x", "NOT_ASSESSED", "NOT_ASSESSED", { status: "NOT_ASSESSED" }))).toBe("INSUFFICIENT");
});

it("shows criteria fulfilled over the total, then the evidence-use groups, each criterion with its own fulfilment state", () => {
  const html = render(held, { coverageMatrix: coverage(iris) });
  expect(html).toContain("기준 충족 0/7 · 확인 필요 7");
  expect(html).not.toContain("근거 있음");
  expect(html).toContain("직접 사용 가능 1");
  expect(html).toContain("조건부·참고용 5");
  expect(html).toContain("자료 없음·부족 1");
  expect(html).toContain("AI 검토 기준");
  expect(html).not.toContain("분류 안 됨");
  expect(html).toContain("질문 check:3");
  // "usable directly" is a separate axis from "fulfilled": check:3 is listed with its own status.
  expect(html).toMatch(/질문 check:3[\s\S]{0,200}미해결/);
});

it("puts the first-screen lines in the agreed order", () => {
  const html = render(held, { coverageMatrix: coverage(iris), progressSummary: progress(action("REVIEW_GAPS", "Review unresolved evidence gaps")) });
  const at = (text: string) => html.indexOf(text);
  expect(at("결론 상태")).toBeGreaterThan(-1);
  expect(at("결론 상태")).toBeLessThan(at("근거 용도"));
  expect(at("근거 용도")).toBeLessThan(at("기준 충족"));
  expect(at("기준 충족")).toBeLessThan(at("반대 근거 탐색"));
  expect(at("반대 근거 탐색")).toBeLessThan(at("다음 확보"));
});

it("shows no refutation count when the counter-evidence search never ran", () => {
  const html = render(held, { coverageMatrix: coverage(iris) });
  expect(html).toContain("반대 근거 탐색: 안 함 · 반박 -");
  expect(html).not.toContain("반박 0");
});

it("shows refutation 0 only for a finished search", () => {
  const done = structuredClone(held) as { portfolio: { hypotheses: Record<string, unknown>[] } };
  done.portfolio.hypotheses = done.portfolio.hypotheses.map(h => ({ ...h, critical_review: { terminal: "UNRESOLVED_NO_RESULTS" } }));
  const html = render(done as unknown as Record<string, unknown>, { coverageMatrix: coverage(iris) });
  expect(html).toContain("반박 0");
  expect(html).not.toContain("반박 -");
});

it("picks the Korean next-step text from the action type and keeps the English server label out of view", () => {
  const html = render(held, { coverageMatrix: coverage(iris), progressSummary: progress(action("REVIEW_GAPS", "Review unresolved evidence gaps")) });
  const visible = html.replace(/<details[\s\S]*?<\/details>/g, "");
  expect(visible).toContain("보류된 기준의 부족한 자료 확인");
  expect(visible).not.toContain("Review unresolved evidence gaps");
  expect(nextActionLabel(action("OPEN_RESULT_DETAIL", "Wait for or inspect the current research attempt", ["RESULT_NOT_PRODUCED"]))).not.toMatch(/[A-Za-z]{4}/);
  expect(nextActionLabel(action("NONE", "No recorded review action"))).toBe("기록된 다음 행동 없음");
  expect(nextActionLabel(action("REVIEW_GAPS", "서버가 준 한국어 안내"))).toBe("보류된 기준의 부족한 자료 확인");
});

it("shows each result's own usage and never turns unknown into zero", () => {
  const entry = (extra: Record<string, unknown> = {}) => ({ operation_id: "op", calls: 2, input_tokens: 10, output_tokens: 5, total_tokens: 15, cached_input_tokens: null,
    unreported_calls: 1, state: "PARTIAL", wall_ms: 180_000, ...extra });
  const status = (usage: Record<string, unknown> | undefined, patch: Partial<ResearchStatus> = {}) => ({ request: { operation_id: "op" },
    result_usage: usage ? { op: usage } : {}, completed_stages: [{ elapsed_ms: 120_000 }, { elapsed_ms: 60_000 }], ...patch }) as ResearchStatus;
  expect(resultUsageLine(status(entry()), "op")).toBe("이 조사: 토큰 15 (부분 관측) · 걸린 시간 3분 · 추정 비용 미확인");
  // no wall time recorded: the stage sum is named as model time, not as elapsed time
  expect(resultUsageLine(status(entry({ wall_ms: null })), "op")).toBe("이 조사: 토큰 15 (부분 관측) · 모델 처리 시간 합 3분 · 추정 비용 미확인");
  expect(resultUsageLine(status(entry({ wall_ms: null }), { completed_stages: [] }), "op")).toBe("이 조사: 토큰 15 (부분 관측) · 시간 미확인 · 추정 비용 미확인");
  expect(resultUsageLine(status(entry({ wall_ms: 20_000 })), "op")).toContain("걸린 시간 1분 미만");
  // a second result of the same thread is no longer hidden, and is not given the stage sum of another attempt
  expect(resultUsageLine(status(entry({ operation_id: "later" }), { request: { operation_id: "op" }, result_usage: { later: entry({ operation_id: "later", wall_ms: null }) } }), "later"))
    .toBe("이 조사: 토큰 15 (부분 관측) · 시간 미확인 · 추정 비용 미확인");
  expect(resultUsageLine(status(undefined), "op")).toBe("이 결과의 사용량 기록 없음");
  expect(resultUsageLine(status(entry()), "other-op")).toBe("이 결과의 사용량 기록 없음");
  expect(resultUsageLine(undefined, "op")).toBe("이 결과의 사용량 기록 없음");
  expect(resultUsageLine({ request: { operation_id: "op" } } as ResearchStatus, "op")).toBe("이 결과의 사용량 기록 없음");
  expect(resultUsageLine(status(entry({ total_tokens: null, state: "UNKNOWN" })), "op")).toBe("이 조사: 토큰 미확인 · 걸린 시간 3분 · 추정 비용 미확인");
});

it("says how many finished stages a resumed run reused and counts automatic retries after a cut-off", () => {
  const entry = (extra: Record<string, unknown> = {}) => ({ operation_id: "op", calls: 2, input_tokens: 10, output_tokens: 5, total_tokens: 15, cached_input_tokens: null,
    unreported_calls: 0, state: "OBSERVED", wall_ms: 180_000, ...extra });
  const status = (usage: Record<string, unknown>, patch: Partial<ResearchStatus> = {}) => ({ request: { operation_id: "op" }, result_usage: { op: usage }, ...patch }) as ResearchStatus;
  const resumed = resultUsageLine(status(entry(), { stage_reuse: { reused: 2, new: 3 } }), "op");
  expect(resumed).toBe("이 조사: 토큰 15 · 걸린 시간 3분 · 완료된 2단계 재사용 · 새로 부른 단계 3 · 추정 비용 미확인");
  // a plain run has no reuse wording, and another result is never given this run's counts
  expect(resultUsageLine(status(entry(), { stage_reuse: { reused: 0, new: 5 } }), "op")).not.toContain("재사용");
  expect(resultUsageLine(status(entry({ operation_id: "later" }), { result_usage: { later: entry({ operation_id: "later" }) }, stage_reuse: { reused: 2, new: 3 } }), "later")).not.toContain("재사용");
  const retried = resultUsageLine(status(entry({ auto_retries: 1, calls: 3 })), "op");
  expect(retried).toBe("이 조사: 토큰 15 · 걸린 시간 3분 · 자동 재시도 1회(연결 끊김) · 추정 비용 미확인");
  expect(resultUsageLine(status(entry({ auto_retries: 0 })), "op")).not.toContain("자동 재시도");
  const widened = resultUsageLine(status(entry({ by_purpose: { MEMORY_QUERY_EXPANSION: { label: "기억 검색어 넓히기", calls: 1, total_tokens: 220 } } })), "op");
  expect(widened).toBe("이 조사: 토큰 15 · 걸린 시간 3분 · 그중 기억 검색어 넓히기 토큰 220 · 추정 비용 미확인");
  const unknown = resultUsageLine(status(entry({ by_purpose: { MEMORY_QUERY_EXPANSION: { label: "기억 검색어 넓히기", calls: 1, total_tokens: null } } })), "op");
  expect(unknown).toContain("그중 기억 검색어 넓히기 토큰 미확인");
});


it("renders the usage line under a result card when one is given", () => {
  expect(render(held, { usageLine: "이 결과의 사용량 기록 없음" })).toContain("이 결과의 사용량 기록 없음");
  expect(render(held)).not.toContain("사용량 기록 없음");
});

