// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ExecutionHold } from "../api/research";
import { ResearchResultCard } from "./ResearchResultCard";

type Json = Record<string, unknown>;
const state = vi.hoisted(() => ({ calls: [] as { method: string; input: Json }[], error: null as number | null, gate: null as Promise<void> | null }));
vi.mock("../api/rpcClient", async importOriginal => {
  const original = await importOriginal<typeof import("../api/rpcClient")>();
  return { ...original, rpc: async (method: string, input: Json) => {
    state.calls.push({ method, input });
    if (state.gate) await state.gate;
    if (state.error !== null) throw new original.RpcError("synthetic rejection", state.error, {});
    return { value: { execution: { state: "INVALIDATED" } }, state: "SUCCEEDED", operation_id: "" };
  } };
});

const PENDING: ExecutionHold = {
  schema_version: "1.0.0", reason_code: "RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED", plan_execution_id: "execution:1", execution_revision: 7,
  attempt_id: "attempt:1", attempt_state: "SUCCEEDED",
  input_difference: { same: false, previous_count: 3, current_count: 4, removed: ["file-a"], added: ["file-b", "file-c"] },
  exits: ["CLEAR_AND_RERUN"],
};
const OTHER: ExecutionHold = { schema_version: "1.0.0", reason_code: "RESEARCH_TEST_TEMPLATE_UNAVAILABLE", plan_execution_id: null, execution_revision: null,
  attempt_id: null, attempt_state: null, input_difference: null, exits: [] };

let root: Root | undefined; let container: HTMLDivElement; let client: QueryClient; let retry: ReturnType<typeof vi.fn>;
const settle = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); }); };
async function mount(hold: ExecutionHold | null) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  retry = vi.fn();
  await act(async () => root!.render(<QueryClientProvider client={client}>
    <ResearchResultCard state="SUCCEEDED" result={{ answer: "부분 답변입니다", answer_status: "PARTIAL_HOLD" }} onDetail={() => {}} onRetry={retry}
      executionHold={hold} projectId="project:radar" holdCauseRef={"c".repeat(64)}/></QueryClientProvider>));
  await settle();
}
beforeEach(() => { state.calls = []; state.error = null; state.gate = null; });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); document.body.innerHTML = ""; vi.restoreAllMocks(); });

const visible = (scope: ParentNode = document.body) => { const copy = (scope as HTMLElement).cloneNode(true) as HTMLElement; copy.querySelectorAll("details").forEach(n => n.remove()); return copy.textContent ?? ""; };
const button = (label: string) => [...document.body.querySelectorAll("button")].find(b => b.textContent?.trim() === label) ?? null;
const click = async (element: Element | null) => { expect(element).not.toBeNull(); await act(async () => (element as HTMLElement).click()); await settle(); };
const type = async (value: string) => { const box = document.body.querySelector("textarea")!; await act(async () => { const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!; set.call(box, value); box.dispatchEvent(new Event("input", { bubbles: true })); }); await settle(); };
const clearCalls = () => state.calls.filter(call => call.method === "execution/invalidate");

it("tells in plain Korean that the same test was already run, what differs in its inputs, and what can be done", async () => {
  await mount(PENDING);
  const text = visible(container);
  expect(text).toContain("같은 시험이 앞서 한 번 실행됐지만 그 결과가 아직 확정되지 않아서");
  expect(text).toContain("다시 실행하지 않았습니다");
  expect(text).toContain("이전 3개");
  expect(text).toContain("지금 4개");
  expect(text).toContain("빠진 것 1개");
  expect(text).toContain("새로 들어온 것 2개");
  expect(text).toContain("지워지지 않고 그대로 남습니다");
  expect(button("정리하고 새로 실행")).not.toBeNull();
  for (const code of ["RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED", "attempt:1", "execution:1", "file-a"]) {
    expect(text, code).not.toContain(code);
    expect(container.innerHTML, code).toContain(code); // kept, folded under 기술 정보
  }
  expect(clearCalls()).toHaveLength(0);
});

it("says the inputs are the same when they are", async () => {
  await mount({ ...PENDING, input_difference: { same: true, previous_count: 3, current_count: 3, removed: [], added: [] } });
  expect(visible(container)).toContain("입력 파일은 이전과 같습니다");
});

it("does not send without a reason, then clears with the exact request, tells how to go on, fills the question, and never reruns by itself", async () => {
  await mount(PENDING);
  await click(button("정리하고 새로 실행"));
  expect(document.body.querySelector("[role=dialog]")).not.toBeNull();
  await click(button("정리하기"));
  expect(clearCalls()).toHaveLength(0);
  expect(visible()).toContain("정리하는 이유를 적어 주세요");
  await type("   ");
  await click(button("정리하기"));
  expect(clearCalls()).toHaveLength(0);
  await type("입력 파일을 일부러 바꾼 뒤 다시 실행하려고");
  await click(button("정리하기"));
  expect(clearCalls()).toEqual([{ method: "execution/invalidate", input: {
    project_id: "project:radar", plan_execution_id: "execution:1", cause_revision_ref: "c".repeat(64), impact_refs: [],
    clear_pending_attempt_id: "attempt:1", expected_execution_revision: 7, reason: "입력 파일을 일부러 바꾼 뒤 다시 실행하려고",
    input_difference: PENDING.input_difference } }]);
  expect(visible(container)).toContain("정리했습니다. 같은 질문을 다시 보내면 새로 실행합니다");
  expect(retry).toHaveBeenCalledTimes(1);
  expect(button("정리하고 새로 실행")).toBeNull();
  expect(state.calls.every(call => call.method === "execution/invalidate")).toBe(true); // no research is started
});

it("sends one request however often the button is pressed while it is working", async () => {
  let release = () => {}; state.gate = new Promise<void>(resolve => { release = resolve; });
  await mount(PENDING);
  await click(button("정리하고 새로 실행"));
  await type("이유");
  await act(async () => { (button("정리하기") as HTMLElement).click(); (button("정리하기") as HTMLElement).click(); });
  await settle();
  expect(clearCalls()).toHaveLength(1);
  await act(async () => release());
  await settle();
  expect(clearCalls()).toHaveLength(1);
});

it.each([
  [-32031, "그 사이에 실행 상태가 바뀌어"],
  [-32030, "정리할 수 없는 상태입니다"],
  [-32603, "정리하지 못했습니다"],
])("explains a refusal (code %i) in plain words, keeps the button, and reads the state again", async (code, sentence) => {
  state.error = code;
  await mount(PENDING);
  const refetch = vi.spyOn(client, "invalidateQueries");
  await click(button("정리하고 새로 실행"));
  await type("이유");
  await click(button("정리하기"));
  expect(visible()).toContain(sentence);
  expect(visible()).not.toContain("synthetic rejection");
  expect(retry).not.toHaveBeenCalled();
  expect(refetch).toHaveBeenCalledWith({ queryKey: ["research", "project:radar"] });
  expect(button("정리하기")).not.toBeNull();
});

it("shows another kind of hold with no way to clear it", async () => {
  await mount(OTHER);
  const text = visible(container);
  expect(text).toContain("시험을 실행하지 않고 보류했습니다");
  expect(button("정리하고 새로 실행")).toBeNull();
  expect(text).not.toContain("RESEARCH_TEST_TEMPLATE_UNAVAILABLE");
  expect(container.innerHTML).toContain("RESEARCH_TEST_TEMPLATE_UNAVAILABLE");
});

it("adds nothing when there is no hold", async () => {
  await mount(null);
  expect(visible(container)).not.toContain("보류했습니다");
  expect(button("정리하고 새로 실행")).toBeNull();
});
