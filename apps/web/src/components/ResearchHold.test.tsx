import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { ResearchResultCard } from "./ResearchResultCard";

const digest = "a".repeat(64);
const row = (status: "SATISFIED" | "UNRESOLVED" | "NOT_APPLICABLE" | "NOT_ASSESSED", extra: Record<string, unknown> = {}) => ({
  requirement_id: "r-" + Math.random(), target: "RESEARCH_GAP", question: "자료가 현재인가", status, applicability: "APPLICABLE",
  relation: "QUALIFIES", validation: "APPLIED", blocker: "answer:HOLD", review_refs: [], evidence_refs: [], reason_codes: ["answer:HOLD", "APPLIED"], ...extra });
const coverage = (rows: ReturnType<typeof row>[]) => ({ schema_version: "1.0.0" as const, request_revision_digest: digest, availability: "AVAILABLE" as const,
  reason_codes: [], requirement_set_revision_digest: null, coverage_revision_digest: null, rows,
  summary: { satisfied: 0, unresolved: 0, not_applicable: 0, not_assessed: 0, hold_targets: [], reason_codes: [] } });
const progress = (items: string[] = []) => ({ schema_version: "1.0.0" as const, request_revision_digest: digest, result_revision_digest: digest, state: "HOLD" as const,
  currentness: { state: "CURRENT" as const, reasons: [], execution_eligible: true }, progress_items: items, recorded_checks: [], remaining_gaps: [], unknowns: [],
  next_user_action: { schema_version: "1.0.0" as const, action_type: "REVIEW_GAPS" as const, label: "남은 항목 검토", reason_codes: [], requires_permission: false, target: null, basis: {} } });
const visible = (html: string) => html.replace(/<details[\s\S]*?<\/details>/g, "");
const render = (result: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={result} onDetail={() => {}} {...extra}/>);

it("puts two hold lines inside the answer card for a real held IRIS answer", () => {
  const html = render(excerpt, { coverageMatrix: coverage([row("SATISFIED"), row("SATISFIED"), row("UNRESOLVED"), row("NOT_ASSESSED"), row("NOT_APPLICABLE")]) });
  expect(html).toContain("결론 상태: <strong>판단 보류</strong>");
  expect(html).toContain("답할 수 있는 범위: 기준 충족 2/5 · 확인 필요 2");
  expect(html.indexOf("결론 상태")).toBeGreaterThan(html.indexOf("answer-text"));
  expect(html.indexOf("결론 상태")).toBeLessThan(html.indexOf("answer-actions"));
});

it("names the other answer statuses and a missing coverage read", () => {
  expect(render({ answer: "a", answer_status: "ASSESSED_WITH_OPEN_CHECKS" })).toContain("<strong>부분 답변</strong>");
  expect(render({ answer: "a", answer_status: "ASSESSED_FOR_REQUEST" })).toContain("<strong>답변 완료</strong>");
  expect(render({ answer: "a", answer_status: "PARTIAL_HOLD" })).toContain("답할 수 있는 범위: 평가기준 정보를 아직 읽지 못했습니다");
  expect(render({ answer: "a" })).not.toContain("결론 상태");
});

it("splits the criteria table into status and judgment so unresolved is not shown next to APPLIED", () => {
  const html = render({ answer: "a", answer_status: "PARTIAL_HOLD" }, { progressSummary: progress(), coverageMatrix: coverage([row("UNRESOLVED")]) });
  expect(html).toContain("기준 상태");
  expect(html).toContain("판단");
  expect(html).toContain("미해결");
  expect(html).toContain("판단 반영됨");
  expect(visible(html)).not.toMatch(/>\s*APPLIED\s*</);
  expect(visible(html)).not.toContain("answer:HOLD");
  expect(visible(html)).not.toContain("RESEARCH_GAP");
});

it("says where the answer's sources are instead of claiming none were recorded", () => {
  const html = render({ answer: "a", selected_evidence_count: 71 }, { progressSummary: progress(), coverageMatrix: coverage([row("SATISFIED")]) });
  expect(html).toContain("이 검토 요약에 따로 연결된 근거는 없습니다. 답변 근거 71개는 &#x27;근거 원문&#x27;에서 볼 수 있습니다.");
  expect(html).not.toContain("직접 근거 ref가 아직 기록되지 않았습니다");
});

it("counts review stages and keeps their ids out of the visible text", () => {
  const html = render({ answer: "a" }, { progressSummary: progress(["요구조건 2개를 확인했습니다", "research-stage:11111111-aaaa", "research-stage:22222222-bbbb"]) });
  expect(html).toContain("검토 단계 2개");
  expect(visible(html)).not.toContain("research-stage:");
  expect(html).toContain("research-stage:11111111-aaaa");
});

it("words the terminal result marker and keeps other internal codes out of the visible review text", () => {
  // compactList also draws the closed "남은 gap" lists, so the review summary column stands for all of them.
  const html = render({ answer: "a" }, { progressSummary: progress([
    "research-stage:11111111-aaaa", "result:terminal", "requirements_recorded",
    "requirement:req-1:UNRESOLVED", "requirement:req-2:SATISFIED", "EVIDENCE_TOO_OLD", "RESULT_NOT_PRODUCED", "자료가 오래됐습니다",
  ]) });
  const text = visible(html);
  expect(text).toContain("답변이 최종 상태로 기록됐습니다");
  expect(text).toContain("평가 조건이 기록됐습니다");
  expect(text).toContain("아직 답변이 만들어지지 않았습니다");
  expect(text).toContain("자료가 오래됐습니다");
  expect(text).toContain("검토 단계 1개");
  expect(text).toContain("조건별 평가 2건");
  expect(text).toContain("기록된 상세 코드 1개");
  for (const code of ["result:terminal", "requirement:req-1", "requirements_recorded", "EVIDENCE_TOO_OLD", "RESULT_NOT_PRODUCED", "research-stage:"]) {
    expect(text).not.toContain(code);
  }
  for (const code of ["research-stage:11111111-aaaa", "requirement:req-1:UNRESOLVED", "EVIDENCE_TOO_OLD"]) expect(html).toContain(code);
});
