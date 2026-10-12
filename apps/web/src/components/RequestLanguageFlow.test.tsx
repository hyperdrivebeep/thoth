// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useResearchSession } from "./useResearchSession";
import { setLocale } from "../i18n/locale";

const state = vi.hoisted(() => ({ fail: true, calls: [] as { method: string; params: Record<string, unknown>; key: string }[] }));
vi.mock("../api/rpcClient", () => ({ rpc: async (method: string, params: Record<string, unknown>, key: string) => {
  state.calls.push({method, params, key});
  if (method === "thread/read") return { value: { operation_state: "SUCCEEDED", request: { response_language: "ko" } } };
  if (method === "thread/input") {
    if (state.fail) throw new Error("response lost");
    return { state: "RUNNING", operation_id: "operation:accepted", value: { contract_version: 2, project_id: "p", thread_id: "t",
      status: "ACCEPTED_RUNNING", operation_id: "operation:accepted", request_epoch: 2 } };
  }
  throw new Error(`Unexpected call ${method}`);
} }));

let session: ReturnType<typeof useResearchSession>;
describe.each([
  { mode: "LOCAL" as const, workspaceId: "workspace:language-flow" },
  { mode: "HOSTED" as const, browserScopeId: "browser:language-flow" },
])("request language in $mode scope", scope => {
function Harness() {
  const epoch = useRef(0);
  session = useResearchSession("p", "t", epoch, () => {}, scope, true);
  return <div>{session.problem}</div>;
}
let root: Root;
let node: HTMLDivElement;
let client: QueryClient;
beforeEach(async () => {
  localStorage.clear(); sessionStorage.clear(); state.calls.length = 0; state.fail = true;
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  client = new QueryClient({defaultOptions: {queries: {retry: false, staleTime: Infinity}, mutations: {retry: false}}});
  client.setQueryData(["workspace-ready"], {value: { response_languages: ["ko", "en"], response_language_scope: "REQUEST" }});
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => { root.render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); node.remove(); localStorage.clear(); sessionStorage.clear(); setLocale("ko"); });

it("changes only the next draft while an uncertain submission replays its frozen language and key", async () => {
  expect(session.responseLanguage).toBe("ko");
  await act(async () => { session.chooseLanguage("en"); });
  await act(async () => { session.setProblem("한국어 질문 원문"); });
  await act(async () => { session.send(); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  const first = state.calls.find(call => call.method === "thread/input")!;
  expect(first.params.response_language).toBe("en");
  expect(first.params.instruction).toBe("한국어 질문 원문");
  await act(async () => { session.chooseLanguage("ko"); });
  expect(session.pendingBlocksNewInput).toBe(true);
  const before = state.calls.length;
  await act(async () => { setLocale("en"); session.send(); });
  expect(state.calls).toHaveLength(before);
  state.fail = false;
  await act(async () => { session.retryPending(); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
  const sent = state.calls.filter(call => call.method === "thread/input");
  expect(sent).toHaveLength(2);
  expect(sent[1]).toEqual(first);
  expect(session.problem).toBe("");
  expect(session.responseLanguage).toBe("ko");
});

it("switching display language has no RPC or pending/draft side effects", async () => {
  await act(async () => { session.setProblem("Keep my draft"); });
  const before = [...state.calls];
  const selectedStore = scope.mode === "LOCAL" ? localStorage : sessionStorage;
  const storage = Object.fromEntries(Object.keys(selectedStore).map(key => [key, selectedStore.getItem(key)]));
  await act(async () => { setLocale("en"); });
  expect(state.calls).toEqual(before);
  expect(session.problem).toBe("Keep my draft");
  expect(session.responseLanguage).toBe("ko");
  for (const [key, value] of Object.entries(storage)) expect(selectedStore.getItem(key)).toBe(value);
});

});
