import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import type { CoverageMatrixRow } from "../api/researchFollowup";
import type { ResearchStatus } from "../api/research";
import { ActionCompare } from "./ActionCompare";
import { AskedQuestionContext } from "./askedQuestion";
import { HypothesisFields } from "./HypothesisFields";
import { hypothesisRows } from "./hypothesisView";
import { ComposerUsage } from "./ResearchComposer";
import { ResearchResultCard } from "./ResearchResultCard";
import { resultUsageLine, usageShownUnderResult } from "./resultUsage";

// The card says each fact once: the status and the counts in the header tags, each kind of unknown
// under one name, the ids and originals in one folded block. Nothing that says "not confirmed" is lost.

const digest = "a".repeat(64);
type Row = CoverageMatrixRow;
const row = (id: string, status: Row["status"], extra: Partial<Row> = {}): Row => ({
  requirement_id: id, target: "time", question: "질문 " + id, status, applicability: "APPLICABLE",
  relation: status === "SATISFIED" ? "SUPPORTS" : "INSUFFICIENT", validation: "APPLIED", blocker: "RESEARCH_GAP",
  review_refs: [], evidence_refs: ["span:" + id], reason_codes: ["CODE_" + id.toUpperCase()], ...extra });
const coverage = (rows: Row[]) => ({ schema_version: "1.0.0" as const, request_revision_digest: digest, availability: "AVAILABLE" as const,
  reason_codes: [], requirement_set_revision_digest: null, coverage_revision_digest: null, rows,
  summary: { satisfied: 0, unresolved: rows.length, not_applicable: 0, not_assessed: 0, hold_targets: [], reason_codes: [] } });
const action = (action_type: "REVIEW_GAPS" | "NONE", label: string) =>
  ({ schema_version: "1.0.0" as const, action_type, label, reason_codes: [], requires_permission: false, target: null, basis: {} });
const progress = (next: ReturnType<typeof action>, state: "COMPLETE" | "HOLD" = "COMPLETE") => ({
  schema_version: "1.0.0" as const, request_revision_digest: digest, result_revision_digest: digest, state,
  currentness: { state: "CURRENT" as const, reasons: [], execution_eligible: true },
  progress_items: ["result:terminal", "research-stage:11111111-aaaa", "research-stage:22222222-bbbb"],
  recorded_checks: ["requirements_recorded", "requirement:bound:0:SATISFIED"],
  remaining_gaps: state === "HOLD" ? ["REQUIREMENT_NOT_ASSESSED"] : [],
  unknowns: state === "HOLD" ? ["COVERAGE_UNAVAILABLE"] : [],
  next_user_action: next });
// The server's own wording of what the assessment left open; the card shows these in Korean.
const OPEN = ["evidence is extracted but not support-verified", "counterevidence has not been checked", "comparison conditions have not been confirmed"];
const OPEN_KO = ["근거는 뽑았지만 뒷받침하는지 아직 확인하지 않았습니다.", "반대 근거는 아직 찾아보지 않았습니다.", "비교 조건이 같은지 아직 확인하지 못했습니다."];
const withOpen = (status: string) => ({ ...excerpt, answer_status: status, assessment: { missing_items: OPEN } }) as Record<string, unknown>;
const render = (result: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={result} onDetail={() => {}} {...extra}/>);
const count = (html: string, text: string) => html.split(text).length - 1;
const withoutDetails = (html: string) => html.replace(/<details[\s\S]*?<\/details>/g, "");
const holdBlock = (html: string) => html.slice(html.indexOf("result-hold-details"), html.indexOf("conversation-findings"));

it("says a finished answer's status once and folds every technical detail of the review basis into one block", () => {
  const html = render(withOpen("ASSESSED_FOR_REQUEST"), {
    coverageMatrix: coverage([row("a", "SATISFIED"), row("b", "SATISFIED")]),
    progressSummary: progress(action("NONE", "No recorded review action")),
  });
  expect(count(html, "답변 완료")).toBe(1);
  expect(count(html, "확인 필요 0")).toBe(1);
  const block = holdBlock(html);
  expect(count(block, "<summary>기술 정보</summary>")).toBe(1);
  // The ids, codes and server originals of every part are all inside that one block, under the part's name.
  const technical = /<details class="connection-tech result-technical">[\s\S]*?<\/details>/.exec(block)?.[0] ?? "";
  for (const kept of ["research-stage:11111111-aaaa", "requirement:bound:0:SATISFIED", "span:a", "CODE_B", "No recorded review action", ...OPEN]) {
    expect(technical, kept).toContain(kept);
  }
  expect(withoutDetails(block)).not.toMatch(/research-stage:|requirement:bound|CODE_|No recorded review action|support-verified/);
});

it("names the answer checks and what the assessment left open differently, with a line saying they are separate", () => {
  const html = render(withOpen("ASSESSED_FOR_REQUEST"), {
    coverageMatrix: coverage([row("a", "SATISFIED")]),
    progressSummary: progress(action("NONE", "No recorded review action")),
  });
  const visible = withoutDetails(html);
  expect(visible).toContain("답변 점검 1개 중 충족 1 · 확인 필요 0");
  expect(visible).toContain("판정이 확인하지 않은 것");
  expect(visible).toContain("답변 점검 항목과는 별개입니다.");
  expect(visible).not.toContain("아직 확인할 내용");
  // Every open condition the server listed is still shown, once each.
  for (const sentence of OPEN_KO) expect(count(visible, sentence), sentence).toBe(1);
});

it("leaves out the action box when no action is required, so it cannot contradict the model's proposed next steps", () => {
  const none = render(withOpen("ASSESSED_FOR_REQUEST"), { progressSummary: progress(action("NONE", "No recorded review action")) });
  expect(withoutDetails(none)).not.toContain("지금 필요한 조치");
  expect(withoutDetails(none)).not.toContain("기록된 다음 행동 없음");
  expect(none).toContain("다음에 해볼 일");

  const needed = render(withOpen("PARTIAL_HOLD"), { progressSummary: progress(action("REVIEW_GAPS", "Review unresolved evidence gaps"), "HOLD") });
  const visible = withoutDetails(needed);
  expect(count(visible, "지금 필요한 조치")).toBe(1);
  // The action is said once (it used to be in the summary lines and again in the action box).
  expect(count(visible, "보류된 기준의 부족한 자료 확인")).toBe(1);
  expect(visible).not.toContain("다음 확보");
});

it("keeps every reason of a held answer after the repeated lines are gone", () => {
  const html = render(withOpen("PARTIAL_HOLD"), {
    coverageMatrix: coverage([row("a", "UNRESOLVED", { blocker: "answer:HOLD" }), row("b", "NOT_ASSESSED")]),
    progressSummary: progress(action("REVIEW_GAPS", "Review unresolved evidence gaps"), "HOLD"),
  });
  const visible = withoutDetails(html);
  const reasons = [
    "판단 보류", // the status tag
    "답변 점검 2개 중 충족 0 · 확인 필요 2", // the count tag
    "미해결", "미평가", // each row's own state in the checks table
    "보류된 기준의 부족한 자료 확인", // the action needed now
    ...OPEN_KO, // what the assessment did not check
  ];
  for (const reason of reasons) expect(visible, reason).toContain(reason);
  // The server's lists of remaining gaps and unknowns are still offered (a folding part of the summary).
  expect(visible).toContain("확인한 항목과 남은 확인 사항");
});

it("says the missing answer checks in the body, because no header tag can", () => {
  const html = render(withOpen("PARTIAL_HOLD"));
  expect(html).toContain("답변 점검 항목 정보를 아직 읽지 못했습니다");
  expect(html).not.toContain("답변 점검 0개");
});

const usageStatus = (total: number | null, part: number | null) => ({
  request: { operation_id: "op" }, completed_stages: [],
  result_usage: { op: { operation_id: "op", calls: 2, input_tokens: null, output_tokens: null, total_tokens: total, cached_input_tokens: null,
    unreported_calls: 0, state: "COMPLETE", wall_ms: 20_000, by_purpose: { MEMORY_QUERY_EXPANSION: { label: "memory", calls: 1, total_tokens: part } } } },
}) as unknown as ResearchStatus;

it("says an unknown token count once, and still shows a share when either number is known", () => {
  const bothUnknown = resultUsageLine(usageStatus(null, null), "op");
  expect(count(bothUnknown, "미확인")).toBe(2); // tokens and estimated cost, not the share a third time
  expect(bothUnknown).not.toContain("기억 검색어 넓히기");
  expect(resultUsageLine(usageStatus(1200, null), "op")).toContain("기억 검색어 넓히기");
  expect(resultUsageLine(usageStatus(null, 300), "op")).toContain("기억 검색어 넓히기");
  expect(resultUsageLine(usageStatus(1200, 300), "op")).toContain("300");
});

it("keeps the usage line under the question box until a result card carries it", () => {
  const result = { result: { answer: "a" } } as unknown as ResearchStatus["current_result"];
  // While research runs there is no card for it yet.
  expect(usageShownUnderResult({ operation_state: "RUNNING", current_result: result })).toBe(false);
  // Finished, but the result is not on screen (not linked yet, failed or cancelled without a result).
  expect(usageShownUnderResult({ operation_state: "SUCCEEDED", current_result: null })).toBe(false);
  expect(usageShownUnderResult({ operation_state: "FAILED", current_result: null })).toBe(false);
  expect(usageShownUnderResult(undefined)).toBe(false);
  // Finished with its result card: the card's own usage line says it.
  expect(usageShownUnderResult({ operation_state: "SUCCEEDED", current_result: result })).toBe(true);
  const card = render({ answer: "a", answer_status: "ASSESSED_FOR_REQUEST" }, { usageLine: resultUsageLine(usageStatus(null, null), "op") });
  expect(count(card, "이 조사: 토큰 미확인")).toBe(1);
});

it("draws the usage line under the question box exactly when no result card carries it", () => {
  const usage = { input_tokens: 5, output_tokens: 7, total_tokens: 12, state: "OBSERVED", unreported_calls: 0, cumulative_token_limit_enforced: false };
  const dock = (operation_state: string, current_result: unknown) =>
    renderToStaticMarkup(<ComposerUsage hidden={false} status={{ operation_state, current_result, usage } as unknown as ResearchStatus} />);
  expect(dock("RUNNING", null)).toContain("사용 토큰 12");
  // The run has ended and its card is not drawn yet: the usage must not disappear from the screen.
  expect(dock("SUCCEEDED", null)).toContain("사용 토큰 12");
  expect(dock("FAILED", null)).toContain("사용 토큰 12");
  expect(dock("SUCCEEDED", { result: { answer: "a" } })).toBe("");
  expect(renderToStaticMarkup(<ComposerUsage hidden status={undefined} />)).toBe("");
});

const actionCard = (patch: Record<string, unknown>) => {
  const result = structuredClone(excerpt) as unknown as { action_plan: { alternatives: Record<string, unknown>[] } };
  result.action_plan.alternatives = [{ ...result.action_plan.alternatives[0], ...patch }];
  return renderToStaticMarkup(<ActionCompare result={result as unknown as Record<string, unknown>}/>);
};

it("says once that an action may run without approval, in words, and keeps the recorded codes folded", () => {
  const html = actionCard({ risk_tier: "R0", execution_authority: "AUTO_R0", state: "AUTO_ALLOWED" });
  const visible = withoutDetails(html).replace(/ (title|class)="[^"]*"/g, "");
  expect(count(visible, "승인 없이 실행 가능")).toBe(1);
  expect(visible).toContain("위험 낮음");
  expect(visible).not.toMatch(/R0|AUTO_ALLOWED|자동 실행/);
  expect(html).toMatch(/<details[^>]*>[\s\S]*R0 · AUTO_R0 · AUTO_ALLOWED[\s\S]*<\/details>/);
  // A state that says something else keeps its own tag.
  const pending = withoutDetails(actionCard({ risk_tier: "R3", execution_authority: "HUMAN_REQUIRED_R3", state: "APPROVAL_PENDING" }));
  expect(pending).toContain("위험 높음");
  expect(pending).toContain("사람 승인 필요");
  expect(pending).toContain("승인 대기");
});

const hypothesisCard = (observed: string, asked: string | null) => {
  const base = hypothesisRows(excerpt as unknown as Record<string, unknown>)[0];
  return renderToStaticMarkup(
    <AskedQuestionContext.Provider value={asked}>
      <HypothesisFields row={{ ...base, observed }} />
    </AskedQuestionContext.Provider>,
  );
};

it("leaves the observation line out only when it is the question itself, character for character", () => {
  const asked = "LAB-42 지연 조건을 요약해줘\n\n비 오는 날 조건만 다시 봐줘";
  expect(hypothesisCard(asked, asked)).not.toContain("hypothesis-observed");
  expect(hypothesisCard("  " + asked + "\n", asked)).not.toContain("hypothesis-observed");
  // One character of difference, or a real observation, is shown.
  expect(hypothesisCard(asked + ".", asked)).toContain("hypothesis-observed");
  expect(hypothesisCard("비 오는 날 지연이 12 ms 늘었다", asked)).toContain("비 오는 날 지연이 12 ms 늘었다");
  // Without a known question (history, examples) nothing is hidden, and an empty one still says so.
  expect(hypothesisCard(asked, null)).toContain("hypothesis-observed");
  expect(hypothesisCard("", asked)).toContain("hypothesis-observed");
});
