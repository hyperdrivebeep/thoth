import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { DecisionDelta } from "../../api/researchFollowup";
import { DecisionDeltaView } from "./DecisionDeltaComparison";

const digest = "a".repeat(64);
const state = (status: string, relation = "QUALIFIES", validation = "INCONCLUSIVE", blocker = "RESEARCH_GAP") => ({ status, relation, validation, blocker });
const item = (requirement_id: string, question: string, match: "SAME" | "ADDED" | "REMOVED", before: object | null, after: object | null, extra: Record<string, unknown> = {}) =>
  ({ requirement_id, target: "RESEARCH_GAP", question, match, before, after, evidence_refs_added: [], evidence_refs_removed: [], unchanged_hold: false, ...extra });
const delta = (criteria: unknown[], criteria_state: "AVAILABLE" | "UNAVAILABLE" = "AVAILABLE"): DecisionDelta => ({
  schema_version: "1.0.0", contract_version: 2, project_id: "p", thread_id: "t",
  before: { request_revision_digest: digest, result_revision_digest: digest }, after: { request_revision_digest: digest, result_revision_digest: digest },
  state: "CHANGED", reason_state: "UNKNOWN_REASON", reason_codes: [], reason_refs: [],
  basis_currentness: { before: { state: "CURRENT", reasons: [], execution_eligible: true }, after: { state: "CURRENT", reasons: [], execution_eligible: true } },
  groups: [{ kind: "CONTENT", trace_paths: ["/result/answer"], changes: [{ path: "/result/answer", before_present: true, after_present: true, before: "이전 답", after: "새 답" }] }],
  criteria, criteria_state,
}) as DecisionDelta;
const render = (value: DecisionDelta) => renderToStaticMarkup(<DecisionDeltaView delta={value}/>);
const visible = (html: string) => html.replace(/<details[\s\S]*?<\/details>/g, "");

it("groups criteria into changed, still on hold, and new or gone", () => {
  const html = render(delta([
    item("check:0", "적용 문서 버전이 현재인가", "SAME", state("UNRESOLVED"), state("SATISFIED", "SUPPORTS", "APPLIED", ""), { evidence_refs_added: ["span:new-1", "span:new-2"] }),
    item("check:1", "센서 조합이 명시되는가", "SAME", state("UNRESOLVED"), state("UNRESOLVED"), { unchanged_hold: true }),
    item("check:2", "시험 환경이 적혀 있는가", "ADDED", null, state("UNRESOLVED")),
    item("check:3", "옛 기준 질문", "REMOVED", state("UNRESOLVED"), null),
  ]));
  const text = visible(html);
  expect(text).toContain("바뀐 기준 1");
  expect(text).toContain("계속 보류 1");
  expect(text).toContain("새로 생긴·없어진 기준 2");
  expect(text.indexOf("바뀐 기준")).toBeLessThan(text.indexOf("계속 보류"));
  expect(text.indexOf("계속 보류")).toBeLessThan(text.indexOf("새로 생긴·없어진 기준"));
  expect(text).toMatch(/적용 문서 버전이 현재인가[\s\S]{0,200}미해결[\s\S]{0,40}충족/);
  expect(text).toContain("추가된 근거 2개");
  expect(text).toContain("보류 사유: 조사 공백 → 없음");
  expect(text).toContain("센서 조합이 명시되는가");
  expect(text).toMatch(/시험 환경이 적혀 있는가[\s\S]{0,300}새로 생김/);
  expect(text).toMatch(/옛 기준 질문[\s\S]{0,300}없어짐/);
  expect(text).not.toMatch(/RESEARCH_GAP|span:new/);
  expect(html).toContain("span:new-1");
});

it("counts a criterion that stayed fulfilled without listing it as a change", () => {
  const same = state("SATISFIED", "SUPPORTS", "APPLIED", "");
  const text = visible(render(delta([item("check:0", "그대로 충족된 기준", "SAME", same, same)])));
  expect(text).toContain("바뀐 기준 0");
  expect(text).toContain("그대로 충족 1");
  expect(text).not.toContain("그대로 충족된 기준");
});

it("keeps the answer-level diff folded under detail changes once criteria are shown", () => {
  const html = render(delta([item("check:0", "기준", "SAME", state("UNRESOLVED"), state("UNRESOLVED"), { unchanged_hold: true })]));
  expect(html).toMatch(/<details[^>]*class="decision-delta-details"[^>]*><summary>세부 변경<\/summary>[\s\S]*답변 전체 변경/);
  expect(visible(html)).not.toContain("서버 기록 변경");
});

it("says criteria could not be compared instead of listing everything as new", () => {
  const html = render(delta([], "UNAVAILABLE"));
  expect(html).toContain("기준별 변화를 읽지 못했습니다");
  expect(html).not.toContain("바뀐 기준");
  expect(html).not.toContain("decision-delta-details");
  expect(html).toContain("답변 전체 변경");
});

