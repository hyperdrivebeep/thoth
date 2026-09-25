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

it.each(["history-denied", "mixed"])("keeps denied cached answers hidden when the following read times out: %s", async denial => {
  await mount(denial === "mixed" ? 2 : 1);
  await returnToWindow(denial);
  await settle(() => !container.textContent?.includes("저장된 답변 1"));
  await returnToWindow("temporary");
  await settle(() => client.getQueryState(["conversation", "p", "t"])?.fetchStatus === "idle");
  expect(container.textContent).not.toContain("저장된 답변 1");
  expect(container.textContent).not.toContain("저장된 답변 2");
  await act(async () => { root!.unmount(); });
  root = createRoot(container);
  await act(async () => { root!.render(<QueryClientProvider client={client}><ConversationTimeline projectId="p" threadId="t" status={status} onDetail={() => undefined}/></QueryClientProvider>); });
  await settle(() => client.getQueryState(["conversation", "p", "t"])?.fetchStatus === "idle");
  expect(container.textContent).not.toContain("저장된 답변 1");
  await returnToWindow("success");
  await settle(() => Boolean(container.textContent?.includes("저장된 답변 1")));
});
