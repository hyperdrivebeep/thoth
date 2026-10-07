// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { HypothesisCompare } from "./HypothesisCompare";
import { hypothesisRows } from "./hypothesisView";
import { TestOrderSection } from "./TestOrderSection";

const BANNED = /순위|\d\s*점|\d\s*%|통과|해결|승인 완료/;
const LEAK = /expected_by_hypothesis|refutation_conditions|hypothesis_id|hypothesis:object|undefined|\bnull\b/;

type Json = Record<string, unknown>;
const ids = () => (excerpt as unknown as { portfolio: { hypotheses: { hypothesis_id: string }[] } }).portfolio.hypotheses.map(item => item.hypothesis_id);
const rows = (...pairs: [number, string, string[]?][]) => pairs.map(([at, expected, basis]) => ({ hypothesis_id: ids()[at], expected, basis: basis ?? [] }));
const test = (id: string, procedure: string, table?: Json[], extra: Json = {}): Json => ({ test_id: id, procedure_candidate: procedure, expected_if_true: "맞으면", expected_if_alternative: "다르면", reversibility: "FULL", estimated_cost: "1",
  ...(table ? { expected_by_hypothesis: table } : {}), ...extra });
// Three hypotheses; the tests are set on the first; the given conditions are the model's.
const result = (tests: Json[], conditions?: string[]) => {
  const copy = structuredClone(excerpt) as unknown as { portfolio: { hypotheses: Json[] } };
  copy.portfolio.hypotheses.forEach(item => { item.discriminating_tests = []; });
  copy.portfolio.hypotheses[0].discriminating_tests = tests;
  if (conditions) copy.portfolio.hypotheses[0].refutation_conditions = conditions;
  return copy as unknown as Record<string, unknown>;
};
const order = (html: string, names: string[]) => names.map(name => html.indexOf(name));
const sorted = (positions: number[]) => positions.every((value, at) => value >= 0 && (at === 0 || value > positions[at - 1]));
const section = (value: Record<string, unknown>) => renderToStaticMarkup(<TestOrderSection rows={hypothesisRows(value)} actions={[]}/>);

it("counts a test with every hypothesis's expected result and puts the sharper one first, with a whole-number sentence", () => {
  const html = section(result([
    test("t1", "두 가설을 가르지 못하는 시험", rows([0, "나쁘다"], [1, "나쁘다"], [2, "같다"])),
    test("t2", "세 가설을 모두 가르는 시험", rows([0, "A"], [1, "B"], [2, "C"])),
  ]));
  expect(sorted(order(html, ["세 가설을 모두 가르는 시험", "두 가설을 가르지 못하는 시험"]))).toBe(true);
  expect(html).toContain("먼저 해 볼 시험");
  expect(html).toContain("결과에 따라 원인 후보를 최대 1개로 줄입니다");
  expect(html).toContain("결과에 따라 원인 후보를 최대 2개로 줄입니다");
  expect(html).not.toContain("얼마나 가르는지 아직 셀 수 없는 시험");
  expect(html).not.toMatch(BANNED);
});

it("treats a hypothesis whose expectation is not known as staying under every result, and a missing row as not countable", () => {
  const html = section(result([
    test("t1", "모름이 섞인 시험", rows([0, "A"], [1, "B"], [2, "모름"])),
    test("t2", "행이 빠진 시험", rows([0, "A"], [1, "B"])),
  ]));
  expect(html).toContain("결과에 따라 원인 후보를 최대 2개로 줄입니다");
  expect(html).toContain("얼마나 가르는지 아직 셀 수 없는 시험");
  expect(sorted(order(html, ["모름이 섞인 시험", "행이 빠진 시험"]))).toBe(true);
});

it("keeps the old behaviour for a result without tables: everything uncountable, in the order the rule gave before", () => {
  const html = section(result([test("t1", "표 없는 시험"), test("t2", "또 하나", undefined, { estimated_cost: "9" })]));
  expect(html).toContain("얼마나 가르는지 아직 셀 수 없는 시험");
  expect(html).not.toContain("먼저 해 볼 시험");
  expect(sorted(order(html, ["표 없는 시험", "또 하나"]))).toBe(true);
});

let root: Root | undefined; let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); });
async function mount(value: Record<string, unknown>) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(<QueryClientProvider client={new QueryClient()}><HypothesisCompare result={value} projectId="p" canDraft/></QueryClientProvider>));
}
const card = () => node.querySelectorAll<HTMLElement>(".hypothesis-card")[0];

it("shows the model's refutation conditions apart, as an unconfirmed suggestion, and never mixes them with the person's own", async () => {
  await mount(result([test("t1", "시험", rows([0, "A"], [1, "B"], [2, "C"]))], ["두 실행 결과가 같으면 이 가설을 버린다"]));
  const ai = card().querySelector(".ai-refutation")!;
  expect(ai.textContent).toContain("AI 제안(미확정) 기각 조건");
  expect(ai.textContent).toContain("두 실행 결과가 같으면 이 가설을 버린다");
  expect(ai.textContent).toContain("사람이 확인하기 전에는");
  const own = card().querySelector(".refutation-conditions")!; // the person's panel
  expect(own.textContent).toContain("아직 적어 둔 기각 조건이 없습니다");
  expect(own.textContent).not.toContain("두 실행 결과가 같으면");
  expect(ai.contains(own)).toBe(false);
  expect(ai.textContent).not.toMatch(BANNED);
  expect(ai.textContent).not.toMatch(LEAK);
});

it("lists what each hypothesis is expected to give for a test, in words, with how many sources it rests on", async () => {
  await mount(result([test("t1", "시험", rows([0, "나쁘다", ["span:a", "span:b"]], [1, "같다"], [2, "모름"]))]));
  const text = card().querySelector("details.expected-results")!.textContent!;
  expect(text).toContain("각 가설의 예상 결과(AI 제안)");
  expect(text).toContain("가설 1: 나쁘다 · 근거 2개");
  expect(text).toContain("가설 2: 같다");
  expect(text).toContain("가설 3: 모름");
  expect(text).toContain("확률이나 점수가 아닙니다");
  expect(text).not.toMatch(LEAK);
});

it("shows nothing of v3 on an old result", async () => {
  await mount(result([test("t1", "시험")]));
  expect(card().querySelector(".ai-refutation")).toBeNull();
  expect(card().querySelector("details.expected-results")).toBeNull();
  expect(card().textContent).not.toContain("AI 제안");
});
