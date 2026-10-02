// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { HypothesisCompareLive } from "./HypothesisCompareLive";

const state = vi.hoisted(() => ({ requests: [] as Record<string, unknown>[], failRequest: false, calls: [] as { method: string; input: Record<string, unknown> }[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    state.calls.push({ method, input });
    if (method === "hypothesis/review/list") return { value: { requests: state.requests }, state: "SUCCEEDED", operation_id: "" };
    if (method === "hypothesis/read") return { value: { hypothesis: { hypothesis_id: input.hypothesis_id, revision_digest: "d".repeat(64) } }, state: "SUCCEEDED", operation_id: "" };
    if (method === "hypothesis/review/request") {
      if (state.failRequest) throw new Error("synthetic request failure");
      return { value: { request: { request_id: "r1" }, created: true, instruction: "ACCEPTED" }, state: "SUCCEEDED", operation_id: "" };
    }
    return { value: {}, state: "SUCCEEDED", operation_id: "" };
  } }));

let root: Root | undefined; let container: HTMLDivElement;
const result = () => structuredClone(excerpt) as Record<string, unknown>;
const hypotheses = (excerpt as { portfolio: { hypotheses: { hypothesis_id: string }[] } }).portfolio.hypotheses;
const request = (patch: Record<string, unknown>) => ({ request_id: "r1", hypothesis_id: hypotheses[0].hypothesis_id, evidence_ref: null, status: "REVIEWING",
  instruction_state: "ACCEPTED", instruction_status: "ACCEPTED_RUNNING", instruction_failure: null, reason_codes: ["EVIDENCE_INTERPRETATION"], note: "",
  hypothesis_revision_digest: "d".repeat(64), added_evidence_refs: [], created_at: "2026-09-30T00:00:00Z", resolution: null, ...patch });
async function settleUi() { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); }); }
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><HypothesisCompareLive result={result()} projectId="project:p" threadId="thread:t"/></QueryClientProvider>));
  await settleUi();
}
const click = async (element: Element | null) => { await act(async () => (element as HTMLElement).click()); await settleUi(); };
const dialog = () => document.body.querySelector(".bp6-dialog");
const buttonByText = (scope: ParentNode, text: string) => [...scope.querySelectorAll("button")].find(b => b.textContent?.trim() === text) ?? null;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); document.body.innerHTML = ""; state.requests = []; state.failRequest = false; state.calls = []; });

it("offers a review request on every hypothesis card", async () => {
  await mount();
  expect(state.calls[0]).toEqual({ method: "hypothesis/review/list", input: { project_id: "project:p", thread_id: "thread:t" } });
  expect([...container.querySelectorAll("button")].filter(b => b.textContent?.trim() === "재검토 요청")).toHaveLength(hypotheses.length);
});

it("opens and closes the request window without sending anything", async () => {
  await mount();
  await click(buttonByText(container, "재검토 요청"));
  expect(dialog()?.textContent).toContain("이 요청만으로 판단 값이 바뀌지는 않습니다");
  for (const label of ["근거 해석이 다름", "중요한 근거가 빠짐", "출처·시점이 부적절함", "다른 가설과의 관계가 잘못됨", "설명이 부족함", "기타"]) expect(dialog()?.textContent).toContain(label);
  await click(buttonByText(document.body, "닫기"));
  expect(state.calls.some(call => call.method === "hypothesis/review/request")).toBe(false);
});

it("needs at least one reason and sends the reasons, memo and the revision it read", async () => {
  await mount();
  await click(buttonByText(container, "재검토 요청"));
  await click(buttonByText(document.body, "재검토 요청 보내기"));
  expect(dialog()?.textContent).toContain("이유를 하나 이상 고르세요");
  expect(state.calls.some(call => call.method === "hypothesis/review/request")).toBe(false);
  await click(dialog()!.querySelector("input[type=checkbox]"));
  await act(async () => {
    const memo = dialog()!.querySelector<HTMLTextAreaElement>("textarea[aria-label='메모']")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(memo, "표본이 너무 작습니다");
    memo.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click(buttonByText(document.body, "재검토 요청 보내기"));
  const sent = state.calls.find(call => call.method === "hypothesis/review/request")!;
  expect(sent.input).toMatchObject({ project_id: "project:p", thread_id: "thread:t", hypothesis_id: hypotheses[0].hypothesis_id,
    hypothesis_revision_digest: "d".repeat(64), reason_codes: ["EVIDENCE_INTERPRETATION"], note: "표본이 너무 작습니다" });
  expect(state.calls.findIndex(call => call.method === "hypothesis/read")).toBeLessThan(state.calls.findIndex(call => call.method === "hypothesis/review/request"));
  // Blueprint keeps a closing dialog in the DOM for its exit transition.
  for (let i = 0; i < 30 && dialog(); i++) await act(async () => { await new Promise(r => setTimeout(r, 20)); });
  expect(dialog()).toBeNull();
});

it("shows an open request as under review, and as waiting behind the current investigation when it is queued", async () => {
  state.requests = [request({ instruction_status: "QUEUED_AFTER_CURRENT" })];
  await mount();
  expect(container.textContent).toContain("재검토 중");
  expect(container.textContent).toContain("현재 조사 뒤에 반영");
  expect([...container.querySelectorAll("button")].filter(b => b.textContent?.trim() === "재검토 요청")).toHaveLength(hypotheses.length - 1);
});

it("shows the outcome as previous to current once resolved", async () => {
  state.requests = [request({ status: "RESOLVED", resolution: { outcome: "CHANGED", previous_relation: "SUPPORT:3|COUNTER:0|APPRAISAL:UNASSESSED", resulting_relation: "SUPPORT:2|COUNTER:1|APPRAISAL:UNASSESSED" } })];
  await mount();
  expect(container.textContent).toContain("재검토 결과: 변경");
  expect(container.textContent).toContain("이전 뒷받침 3 · 반박 0 → 현재 뒷받침 2 · 반박 1");
  expect(container.textContent).not.toMatch(/SUPPORT|COUNTER|APPRAISAL|CHANGED/);
});

it("says a failed send did not go out and sends the same request again on demand", async () => {
  state.requests = [request({ status: "OPEN", instruction_state: "FAILED", instruction_status: null, instruction_failure: "MODEL_UNAVAILABLE" })];
  await mount();
  expect(container.textContent).toContain("재검토 요청을 보내지 못했습니다");
  await click(buttonByText(container, "다시 보내기"));
  const sent = state.calls.find(call => call.method === "hypothesis/review/request")!;
  expect(sent.input).toMatchObject({ hypothesis_id: hypotheses[0].hypothesis_id, hypothesis_revision_digest: "d".repeat(64), reason_codes: ["EVIDENCE_INTERPRETATION"] });
});

it("keeps the window open with the error when the request is refused", async () => {
  state.failRequest = true;
  await mount();
  await click(buttonByText(container, "재검토 요청"));
  await click(dialog()!.querySelector("input[type=checkbox]"));
  await click(buttonByText(document.body, "재검토 요청 보내기"));
  expect(dialog()?.textContent).toContain("재검토 요청을 보내지 못했습니다");
});
