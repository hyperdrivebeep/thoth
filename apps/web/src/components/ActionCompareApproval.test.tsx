// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { ActionCompare } from "./ActionCompare";

const state = vi.hoisted(() => ({ calls: [] as string[] }));
const auth = { authorization_id: "auth:1", step_id: "step:send", state: "STALE", stale_reason: "MATERIAL_CHANGE", payload: {},
  material_changes: [{ field: "channel", label: "전달 경로", before: "EMAIL", after: "FORM", words: [], attachments: [] }], decision_history: [], created_at: "2026-09-30T00:00:00Z" };
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    state.calls.push(method);
    if (method === "action/plan/read") return { value: { plan: { plan_id: input.plan_id }, authorizations: [auth] }, state: "SUCCEEDED", operation_id: "" };
    if (method === "action/authorization/read") return { value: { authorization: auth, effective_state: "STALE", payload_current: false, review_needed: false }, state: "SUCCEEDED", operation_id: "" };
    return { value: {}, state: "SUCCEEDED", operation_id: "" };
  } }));

let root: Root | undefined; let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); state.calls = []; });
async function mount(result: Record<string, unknown>, projectId?: string) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><ActionCompare result={result} projectId={projectId}/></QueryClientProvider>));
  for (let i = 0; i < 8; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); });
}
const protectedResult = () => {
  const result = structuredClone(excerpt) as unknown as { action_plan: { plan_id?: string; alternatives: Record<string, unknown>[] } };
  result.action_plan.plan_id = "plan:1";
  result.action_plan.alternatives[0].execution_authority = "HUMAN_REQUIRED_R3";
  return result as unknown as Record<string, unknown>;
};

it("shows what changed since approval next to a protected action", async () => {
  await mount(protectedResult(), "project:p");
  expect(state.calls).toContain("action/plan/read");
  expect(node.textContent).toContain("승인 뒤 바뀜: 전달 경로");
});

it("does not read approvals when no action needs one or when no project is given", async () => {
  await mount(protectedResult());
  expect(state.calls).not.toContain("action/plan/read");
  await act(async () => root!.unmount()); node.remove();
  const plain = structuredClone(excerpt) as unknown as Record<string, unknown>;
  await mount(plain, "project:p");
  expect(state.calls).not.toContain("action/plan/read");
});
