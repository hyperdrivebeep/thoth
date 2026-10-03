// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ActionEffort } from "./ActionEffort";

const state = vi.hoisted(() => ({ estimates: [] as unknown[], running: false, readFails: false, calls: [] as { method: string; input: Record<string, unknown> }[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    state.calls.push({ method, input });
    if (method === "action/read") {
      if (state.readFails) throw new Error("synthetic read failure");
      return { value: { research_running: state.running, action: { revision_digest: "a".repeat(64), generation_details: { effort_estimates: state.estimates } } }, state: "SUCCEEDED", operation_id: "" };
    }
    return { value: { action: { revision_digest: "b".repeat(64) } }, state: "SUCCEEDED", operation_id: "" };
  } }));

let root: Root | undefined; let container: HTMLDivElement;
const ai = { dimension: "TIME", band: "MEDIUM", estimator_type: "AI", estimator_ref: "ACTION_PLANNER", basis_text: "담당자 회신이 필요", assumptions: [], created_at: "2026-09-30T00:00:00Z" };
async function mount(initial: unknown[] = [ai]) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><ActionEffort projectId="project:p" actionId="action:1" initial={initial}/></QueryClientProvider>));
  for (let i = 0; i < 10; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); });
}
async function open() { await act(async () => (container.querySelector("button[aria-label='사람 추정 입력 열기']") as HTMLButtonElement).click()); }
async function pick(label: string, value: string) {
  await act(async () => {
    const select = container.querySelector<HTMLSelectElement>(`select[aria-label='${label}']`)!;
    select.value = value; select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
async function type(label: string, value: string) {
  await act(async () => {
    const input = container.querySelector<HTMLInputElement>(`input[aria-label='${label}']`)!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); state.estimates = []; state.running = false; state.readFails = false; state.calls = []; });

it("turns the human estimate button off with a reason while an investigation runs", async () => {
  state.running = true;
  await mount();
  const button = container.querySelector<HTMLButtonElement>("button[aria-label='사람 추정 입력 열기']")!;
  expect(button.disabled).toBe(true);
  expect(container.textContent).toContain("조사가 끝난 뒤 제안할 수 있습니다");
});

it("reads the current record so an estimate added after the result still shows", async () => {
  state.estimates = [ai, { ...ai, band: "HIGH", estimator_type: "HUMAN", estimator_ref: "human:local-user", basis_text: "현장 시험", created_at: "2026-09-30T01:00:00Z" }];
  await mount([ai]);
  expect(state.calls[0]).toEqual({ method: "action/read", input: { project_id: "project:p", action_id: "action:1" } });
  expect(container.textContent).toContain("AI 추정: 보통");
  expect(container.textContent).toContain("사람 추정: 김");
});

it("falls back to the result's own estimates when the record cannot be read", async () => {
  state.readFails = true;
  await mount([ai]);
  expect(container.textContent).toContain("시간: 보통 · AI 추정 · 담당자 회신이 필요");
});

it("sends a person's band with its basis as a new revision bound to the record it read", async () => {
  await mount([ai]);
  await open();
  await pick("시간 추정", "HIGH"); await type("시간 근거", "현장 시험이 필요");
  await act(async () => (container.querySelector("button[aria-label='사람 추정 저장']") as HTMLButtonElement).click());
  const revise = state.calls.find(call => call.method === "action/revise")!;
  expect(revise.input).toMatchObject({ project_id: "project:p", action_id: "action:1", expected_revision_digest: "a".repeat(64), patch: {},
    estimator_ref: "human:local-user", human_effort_estimates: [{ dimension: "TIME", band: "HIGH", basis_text: "현장 시험이 필요" }] });
  expect(JSON.stringify(revise.input)).not.toMatch(/"band":\s*[0-9]/);
});

it("does not send a band without a basis sentence", async () => {
  await mount([ai]);
  await open();
  await pick("비용·품 추정", "LOW");
  await act(async () => (container.querySelector("button[aria-label='사람 추정 저장']") as HTMLButtonElement).click());
  expect(state.calls.some(call => call.method === "action/revise")).toBe(false);
  expect(container.textContent).toContain("근거 한 줄을 적어 주세요");
});
