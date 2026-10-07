// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import type { DiscriminationItem } from "../api/judgmentRecords";
import { ActionCompare } from "./ActionCompare";
import { HypothesisCompare } from "./HypothesisCompare";
import { hypothesisRows } from "./hypothesisView";
import { TestOrderSection } from "./TestOrderSection";

const BANNED = /확률|순위|\d\s*점|\d\s*%|통과|해결|승인 완료/;
const LEAK = /READY|UNCOUNTABLE|COST_UNKNOWN|NOT_EXECUTABLE|RESULT_RECORDED|HYPOTHESIS_ELIMINATED|SINGLE|REPEATED|undefined|\bnull\b/;

type Test = Record<string, unknown>;
const test = (id: string, procedure: string, extra: Test = {}): Test => ({ test_id: id, procedure_candidate: procedure, expected_if_true: "맞으면", expected_if_alternative: "다르면", reversibility: "FULL", ...extra });
// Three hypotheses; the first has three tests, the second one, the third none.
const result = (tests: Test[][]) => {
  const copy = structuredClone(excerpt) as unknown as { portfolio: { hypotheses: Record<string, unknown>[] } };
  copy.portfolio.hypotheses.forEach(item => { item.discriminating_tests = []; });
  tests.forEach((list, at) => { copy.portfolio.hypotheses[at].discriminating_tests = list; });
  return copy as unknown as Record<string, unknown>;
};
const ids = () => (excerpt as unknown as { portfolio: { hypotheses: { hypothesis_id: string }[] } }).portfolio.hypotheses.map(item => item.hypothesis_id);
const render = (value: Record<string, unknown>, discrimination?: DiscriminationItem[]) =>
  renderToStaticMarkup(<TestOrderSection rows={hypothesisRows(value)} actions={[]} discrimination={discrimination}/>);
const order = (html: string, names: string[]) => names.map(name => html.indexOf(name));
const sorted = (positions: number[]) => positions.every((value, at) => value >= 0 && (at === 0 || value > positions[at - 1]));

it("lists a test with a known cost before one whose cost is unknown, and says so without a number", () => {
  const html = render(result([[test("t1", "비용을 모르는 시험"), test("t2", "싼 시험", { estimated_cost: "2" }), test("t3", "비싼 시험", { estimated_cost: "9" })]]));
  expect(sorted(order(html, ["싼 시험", "비싼 시험", "비용을 모르는 시험"]))).toBe(true);
  expect(html).toContain("비용 미상");
  expect(html).toContain("후보를 하나만 줄이는 시험으로 셉니다");
  expect(html).not.toMatch(BANNED);
  expect(html).not.toMatch(LEAK);
});

it("keeps what was made first when everything else is equal, and marks the tie", () => {
  const html = render(result([[test("t1", "먼저 만든 시험", { estimated_cost: "2" }), test("t2", "나중에 만든 시험", { estimated_cost: "2" })]]));
  expect(sorted(order(html, ["먼저 만든 시험", "나중에 만든 시험"]))).toBe(true);
  expect(html).toContain("동점");
});

it("puts a test that can be undone before one that cannot, at the same cost", () => {
  const html = render(result([[test("t1", "되돌릴 수 없는 시험", { estimated_cost: "2", reversibility: "NONE" }), test("t2", "되돌릴 수 있는 시험", { estimated_cost: "2" })]]));
  expect(sorted(order(html, ["되돌릴 수 있는 시험", "되돌릴 수 없는 시험"]))).toBe(true);
});

it("never reads the risk tier the model wrote on a test: the cheaper test goes first whatever the model called it", () => {
  const html = render(result([[test("t1", "모델이 낮다고 쓴 비싼 시험", { risk_tier: "R0", estimated_cost: "9" }), test("t2", "모델이 높다고 쓴 싼 시험", { risk_tier: "R3", estimated_cost: "2" })]]));
  expect(sorted(order(html, ["모델이 높다고 쓴 싼 시험", "모델이 낮다고 쓴 비싼 시험"]))).toBe(true);
});

it("names a test that cannot be run now apart", () => {
  const html = render(result([[test("t1", "지금 못 하는 시험", { executable: false }), test("t2", "할 수 있는 시험")]]));
  expect(html).toContain("지금은 할 수 없는 시험");
  expect(sorted(order(html, ["할 수 있는 시험", "지금 못 하는 시험"]))).toBe(true);
});

it("sets apart a test whose result is already recorded, and a hypothesis eliminated by repeated results, and only marks one eliminated once", () => {
  const [first, second] = ids();
  const html = render(result([[test("t1", "이미 한 시험"), test("t2", "아직 안 한 시험")], [test("t3", "배제된 가설의 시험")]]), [
    { hypothesis_id: first, elimination: "SINGLE", result_history_count: 1, refutation_conditions: [], conditions_history_count: 0,
      results: [{ event_id: "e", hypothesis_id: first, test_id: "t1", observation: "봤다", matched: "ALTERNATIVE", evidence_refs: [], actor_id: "human:local-user", created_at: "2026-10-06T00:00:00+00:00" }] },
    { hypothesis_id: second, elimination: "REPEATED", result_history_count: 2, refutation_conditions: [], conditions_history_count: 0, results: [] },
  ]);
  expect(sorted(order(html, ["아직 안 한 시험", "이미 한 시험", "배제된 가설의 시험"]))).toBe(true);
  expect(html).toContain("결과를 이미 기록한 시험");
  expect(html).toContain("배제된 가설의 시험");
  expect(html).toContain("다른 설명과 맞음"); // the recorded result is shown beside it
  expect(html).toContain("배제(결과 1건)"); // marked, still counted
  expect(html).not.toContain("배제(반복 확인)을 후보에서"); // (the group title says it in words)
  expect(html).not.toMatch(LEAK);
});

it("says the order is a rule and not a score, that plausibility is not used, and that the unknown cause always stays", () => {
  const html = render(result([[test("t1", "시험")]]));
  expect(html).toContain("점수가 아니라 규칙");
  expect(html).toContain("그럴듯함은 순서에 쓰지 않습니다");
  expect(html).toContain("원인을 아직 모른다는 칸은 어떤 결과에서도 남습니다");
  expect(html).not.toMatch(/\d\s*%|확률/);
});

it("shows nothing when no hypothesis has a test", () => {
  expect(render(result([]))).toBe("");
});

let root: Root | undefined; let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); });
it("is the first thing on the hypothesis comparison, above the cards", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(<HypothesisCompare result={result([[test("t1", "비교 화면의 시험")]])}/>));
  const section = node.querySelector(".test-order")!;
  expect(section).toBeTruthy();
  expect(section.compareDocumentPosition(node.querySelector(".hypothesis-card")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

const action = (id: string, specification: string, extra: Record<string, unknown> = {}) => ({ action_id: id, action_family: "READ_ONLY_ANALYSIS", specification, expected_information_value: "정보",
  risk_tier: "R0", execution_authority: "AUTO_R0", reversibility: "FULL", state: "AUTO_ALLOWED", source_refs: [], missing_evidence: [], estimated_cost: null, estimated_seconds: null, ...extra });
const compare = (alternatives: Record<string, unknown>[]) => {
  const copy = structuredClone(excerpt) as unknown as { action_plan: { alternatives: unknown[] } };
  copy.action_plan.alternatives = alternatives;
  return renderToStaticMarkup(<ActionCompare result={copy as unknown as Record<string, unknown>}/>);
};

it("orders the action cards by the same rule and moves a forbidden-level one out of the order, still showing it", () => {
  const html = compare([action("a1", "보호된 싼 행동", { risk_tier: "R3", execution_authority: "HUMAN_REQUIRED_R3", estimated_cost: "1" }), action("a2", "금지 수준 행동", { risk_tier: "R4", execution_authority: "PROHIBITED_R4" }),
    action("a3", "낮은 위험 비싼 행동", { estimated_cost: "8" }), action("a4", "낮은 위험 비용 미상 행동")]);
  const cards = html.split('<section class="detail-card action-card"').slice(1);
  expect(cards).toHaveLength(4);
  expect(sorted(order(cards.join(""), ["낮은 위험 비싼 행동", "낮은 위험 비용 미상 행동", "보호된 싼 행동", "금지 수준 행동"]))).toBe(true);
  expect(html).toContain("순서에서 뺀 행동");
  expect(html).not.toMatch(LEAK);
});
