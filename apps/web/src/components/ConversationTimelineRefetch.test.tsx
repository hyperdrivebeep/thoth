// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ConversationTimeline } from "./ConversationTimeline";
import { rpc, RpcError, RpcTransportError } from "../api/rpcClient";
import type { ResearchStatus } from "../api/research";

vi.mock("../api/rpcClient", async importOriginal => ({
  ...await importOriginal<typeof import("../api/rpcClient")>(), rpc: vi.fn(),
}));

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;
let mode = "success";
const input = (n: number) => ({ request_epoch: n, request_revision_digest: `request-${n}`, operation_id: `op-${n}`,
  text: `question-${n}`, edit_kind: "APPEND", created_at: "2026-09-21T09:45:09Z", authored_text_ref: { revision_digest: `authored-${n}` } });
const result = (n: number) => ({ thread_id: "t", request_epoch: n, answer: `저장된 답변 ${n}` });
const status: ResearchStatus = { project_id: "p", thread_id: "t", cycle_id: "cycle", problem: "question-1",
  lifecycle: "OPEN", execution_state: "IDLE", current_object_ids: [], working_head_digest: "head", operation_state: "SUCCEEDED",
  request: { operation_id: "op-1", authored_text: "question-1" },
  current_result: { operation_id: "op-1", request_ref: { revision_digest: "request-1" }, result: result(1),
    phase: "COMPLETED", state: "CURRENT", completion: "COMPLETE", terminal_reason: "BOUNDED_RESEARCH_COMPLETE",
    basis_digest: "basis", gaps: [], next_steps: [], source_refs: [], record_refs: [] },
};

async function settle(check: () => boolean) {
  for (let i = 0; i < 50; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    if (check()) return;
  }
  throw new Error("Expected conversation state was not rendered");
}

async function mount(turns = 1) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mode = "success";
  vi.mocked(rpc).mockImplementation(async (method, args) => {
    if (method === "thread/activity/list") {
      if (mode === "history-denied") throw new RpcError("denied", -32040, { reason_code: "RESOURCE_SCOPE_ACCESS_DENIED" });
      return { operation_id: "read", state: "SUCCEEDED", value: { conversation: {
        turns: Array.from({ length: turns }, (_, n) => input(n + 1)), next_before_epoch: null, history_limited: false,
      }} };
    }
    if (mode === "temporary" || (mode === "mixed" && args.operation_id === "op-2")) throw new RpcTransportError("RPC transport failed (504)", "HTTP", 504);
    if (mode === "result-denied" || mode === "mixed") throw new RpcError("denied", -32040, { reason_code: "RESOURCE_SCOPE_ACCESS_DENIED" });
    const n = args.operation_id === "op-2" ? 2 : 1;
    return { operation_id: "read", state: "SUCCEEDED", value: { operation_id: `op-${n}`, state: "SUCCEEDED", result: result(n) } };
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  await act(async () => { root!.render(<QueryClientProvider client={client}><ConversationTimeline projectId="p" threadId="t" status={status} onDetail={() => undefined}/></QueryClientProvider>); });
  await settle(() => Boolean(client.getQueryData(["conversation", "p", "t"])));
  expect(container.textContent).toContain("저장된 답변 1");
}

async function returnToWindow(next: string) {
  await act(async () => { focusManager.setFocused(false); });
  mode = next;
  await act(async () => { focusManager.setFocused(true); });
}

afterEach(async () => {
  if (root) await act(async () => { root!.unmount(); });
  root = undefined; client?.clear(); container?.remove(); focusManager.setFocused(undefined); vi.resetAllMocks();
});

it("keeps the saved answer with a stale-read warning after window refocus fails temporarily", async () => {
  await mount(); await returnToWindow("temporary");
  await settle(() => Boolean(container.textContent?.includes("최신 상태는 확인되지 않았습니다")));
  expect(container.textContent).toContain("저장된 답변 1");
  expect(container.textContent).not.toContain("연구를 완료하지 못했습니다");
  expect(vi.mocked(rpc).mock.calls.every(([method]) => ["thread/activity/list", "operation/result/read"].includes(method))).toBe(true);
  await returnToWindow("success");
  await settle(() => !container.textContent?.includes("최신 상태는 확인되지 않았습니다"));
  expect(container.textContent).toContain("저장된 답변 1");
});

it.each(["history-denied", "result-denied", "mixed"])("hides cached and checkpoint answers after refocus encounters %s", async next => {
  await mount(next === "mixed" ? 2 : 1); await returnToWindow(next);
  await settle(() => !container.textContent?.includes("저장된 답변 1"));
  expect(container.textContent).not.toContain("저장된 답변 2");
  expect(container.textContent).toMatch(/접근 권한|현재 권한/);
});

it("never places an earlier answer under the newer failed question while history is unavailable", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(rpc).mockRejectedValue(new RpcTransportError("RPC transport failed (504)", "HTTP", 504));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  const later: ResearchStatus = { ...status, operation_state: "FAILED", current_result: null,
    previous_result: status.current_result, request: { operation_id: "op-2", authored_text: "새 요청의 질문" },
    operation_error: { message: "new request failed" },
  };
  await act(async () => { root!.render(<QueryClientProvider client={client}><ConversationTimeline projectId="p" threadId="t" status={later} onDetail={() => undefined}/></QueryClientProvider>); });
  await settle(() => Boolean(container.textContent?.includes("대화를 불러오지 못했습니다")));
  expect(container.textContent).not.toContain("저장된 답변 1");
});

it("keeps cancellation in the current-request fallback when history is unsupported", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(rpc).mockResolvedValue({ operation_id: "read", state: "SUCCEEDED", value: {} });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  const cancelled: ResearchStatus = { ...status, operation_state: "CANCELLED", current_result: null,
    previous_result: { ...status.current_result!, result: { requirements: {} }, terminal_reason: null, completion: "CHECKPOINT" },
  };
  await act(async () => { root!.render(<QueryClientProvider client={client}><ConversationTimeline projectId="p" threadId="t" status={cancelled} onDetail={() => undefined}/></QueryClientProvider>); });
  await settle(() => Boolean(container.textContent?.includes("연구 실행이 취소되었습니다")));
  expect(container.textContent).toContain("저장된 답변은 없습니다");
  expect(container.textContent).not.toContain("당시 저장된 답변");
});
