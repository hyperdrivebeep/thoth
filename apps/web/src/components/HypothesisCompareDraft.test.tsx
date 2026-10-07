// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { HypothesisCompare } from "./HypothesisCompare";

let root: Root | undefined;
let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); });

// The first hypothesis has two tests, the second has one without an id, the third has none.
const result = () => {
  const copy = structuredClone(excerpt) as unknown as { answer: string; portfolio: { hypotheses: Record<string, unknown>[] }; selected_evidence_refs: unknown[] };
  copy.portfolio.hypotheses[0].discriminating_tests = [
    { test_id: "t1", procedure_candidate: "비 조건과 맑은 조건을 비교한다", expected_if_true: "비 조건이 나쁘다", expected_if_alternative: "두 조건이 같다" },
    { test_id: "t2", procedure_candidate: "센서 설정을 바꿔 다시 잰다", expected_if_true: "개선된다", expected_if_alternative: "그대로다" },
  ];
  copy.portfolio.hypotheses[1].discriminating_tests = [{ procedure_candidate: "id가 없는 시험", expected_if_true: "a", expected_if_alternative: "b" }];
  return copy as unknown as Record<string, unknown>;
};
async function mount(canDraft: boolean) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  const client = new QueryClient();
  await act(async () => root!.render(<QueryClientProvider client={client}><HypothesisCompare result={result()} projectId="p" canDraft={canDraft}/></QueryClientProvider>));
}
const drafts = () => [...node.querySelectorAll("button")].filter(item => item.textContent?.includes("행동 요청 준비"));

it("puts the button on each test that has an id, and only when asked to", async () => {
  await mount(true);
  expect(drafts()).toHaveLength(2);
  const first = node.querySelectorAll<HTMLElement>(".hypothesis-card")[0];
  expect(first.querySelectorAll("button").length).toBeGreaterThanOrEqual(2);
  expect(node.querySelectorAll<HTMLElement>(".hypothesis-card")[1].textContent).not.toContain("행동 요청 준비");
  await act(async () => root!.unmount()); node.remove(); root = undefined;
  await mount(false);
  expect(drafts()).toHaveLength(0);
});
