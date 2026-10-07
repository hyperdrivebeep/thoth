// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import type { DiscriminationItem } from "../api/judgmentRecords";
import { HypothesisCompare } from "./HypothesisCompare";

const calls = vi.hoisted(() => [] as { method: string; input: Record<string, unknown> }[]);
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => { calls.push({ method, input }); return { value: { items: [] }, state: "SUCCEEDED", operation_id: "op" }; } }));

const BANNED = /통과|해결|승인 완료|검증 완료/;
let root: Root | undefined; let node: HTMLDivElement;
beforeEach(() => { calls.length = 0; });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); document.body.innerHTML = ""; });

// The first hypothesis has two tests; the others none.
const result = () => {
  const copy = structuredClone(excerpt) as unknown as { portfolio: { hypotheses: Record<string, unknown>[] } };
  copy.portfolio.hypotheses[0].discriminating_tests = [
    { test_id: "t1", procedure_candidate: "비 조건과 맑은 조건을 비교한다", expected_if_true: "비 조건이 나쁘다", expected_if_alternative: "두 조건이 같다" },
    { test_id: "t2", procedure_candidate: "센서 설정을 바꿔 다시 잰다", expected_if_true: "개선된다", expected_if_alternative: "그대로다" },
  ];
  return copy as unknown as Record<string, unknown>;
};
const first = () => (result().portfolio as { hypotheses: { hypothesis_id: string }[] }).hypotheses[0].hypothesis_id;
const item = (patch: Partial<DiscriminationItem> = {}): DiscriminationItem => ({ hypothesis_id: first(), results: [], result_history_count: 0, refutation_conditions: [], conditions_history_count: 0, elimination: null, ...patch });
async function mount(items: DiscriminationItem[] | undefined, canDraft = true) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><HypothesisCompare result={result()} projectId="p" canDraft={canDraft} discrimination={items}/></QueryClientProvider>));
}
const card = () => node.querySelectorAll<HTMLElement>(".hypothesis-card")[0];
const button = (label: string, scope: ParentNode = document.body) => [...scope.querySelectorAll("button")].find(b => b.textContent?.trim() === label) as HTMLButtonElement | undefined;
const click = async (element: Element | undefined | null) => { expect(element).toBeTruthy(); await act(async () => (element as HTMLElement).click()); await act(async () => { await new Promise(done => setTimeout(done, 0)); }); };
const dialog = () => document.body.querySelector<HTMLElement>('[role="dialog"]')!;
const choose = async (label: string) => click([...dialog().querySelectorAll("label")].find(l => l.textContent?.includes(label))!.querySelector("input"));
const write = async (index: number, text: string) => {
  const area = dialog().querySelectorAll("textarea")[index];
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(area, text); area.dispatchEvent(new Event("input", { bubbles: true })); });
};

it("shows nothing recorded yet, and a button to record on each test", async () => {
  await mount([]);
  expect([...card().querySelectorAll("button")].filter(b => b.textContent?.trim() === "결과 기록")).toHaveLength(2);
  expect(card().textContent).not.toContain("배제");
  expect(card().textContent).toContain("기각 조건");
});

it("has no controls when the screen is not live", async () => {
  await mount(undefined, false);
  expect(card().textContent).not.toContain("결과 기록");
  expect(card().textContent).not.toContain("기각 조건");
});

it("shows the result that counts under its test, who recorded it, and the elimination mark with its note", async () => {
  const done = item({ elimination: "REPEATED", result_history_count: 3, results: [
    { event_id: "e1", hypothesis_id: first(), test_id: "t1", observation: "비 조건 결과가 맑은 조건과 같았다", matched: "ALTERNATIVE", evidence_refs: ["doc://run-7"], actor_id: "human:local-user", created_at: "2026-10-06T01:02:03+00:00" }] });
  await mount([done]);
  const text = card().textContent!;
  expect(text).toContain("다른 설명과 맞음");
  expect(text).toContain("비 조건 결과가 맑은 조건과 같았다");
  expect(text).toContain("doc://run-7");
  expect(text).toContain("배제(반복 확인)");
  expect(text).toContain("지워지지 않고");
  expect([...card().querySelectorAll("button")].filter(b => b.textContent?.trim() === "결과 다시 기록")).toHaveLength(1); // only the test that has a result
  expect(text).not.toMatch(BANNED);
});

it("needs a kind and words, then sends the result with its references and nothing else", async () => {
  await mount([]);
  await click(button("결과 기록", card()));
  await click(button("기록 남기기", dialog()));
  expect(dialog().textContent).toContain("어느 설명과 맞았는지 고르세요");
  expect(calls).toHaveLength(0);
  await choose("다른 설명과 맞음");
  await click(button("기록 남기기", dialog()));
  expect(dialog().textContent).toContain("관찰한 내용을 적어 주세요");
  await write(0, "  비 조건 결과가 맑은 조건과 같았다 ");
  await write(1, "doc://run-7\n\n 시험성적서 3 ");
  await click(button("기록 남기기", dialog()));
  expect(calls).toEqual([{ method: "hypothesis/test/result/record", input: { project_id: "p", hypothesis_id: first(), test_id: "t1", observation: "비 조건 결과가 맑은 조건과 같았다",
    matched: "ALTERNATIVE", evidence_refs: ["doc://run-7", "시험성적서 3"] } }]);
});

it("sends the refutation conditions one per line, drops blank lines, and refuses more than ten", async () => {
  await mount([item({ refutation_conditions: ["두 조건이 같다"], conditions_history_count: 1 })]);
  expect(card().textContent).toContain("두 조건이 같다");
  await click(button("기각 조건 기록", card()));
  await write(0, "A 조건\n\n  B 조건  ");
  await click(button("기록 남기기", dialog()));
  expect(calls).toEqual([{ method: "hypothesis/refutation/record", input: { project_id: "p", hypothesis_id: first(), conditions: ["A 조건", "B 조건"] } }]);
  calls.length = 0;
  await click(button("기각 조건 기록", card()));
  await write(0, Array.from({ length: 11 }, (_, index) => "조건 " + index).join("\n"));
  await click(button("기록 남기기", dialog()));
  expect(dialog().textContent).toContain("10개까지");
  expect(calls).toHaveLength(0);
});
