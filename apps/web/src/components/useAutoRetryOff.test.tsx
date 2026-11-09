// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useAutoRetryOff } from "./useAutoRetryOff";

const state = vi.hoisted(() => ({ calls: [] as { method: string; params: Record<string, unknown> }[], answer: null as unknown }));
vi.mock("../api/rpcClient", () => ({ rpc: async (method: string, params: Record<string, unknown>) => {
  state.calls.push({ method, params });
  if (state.answer instanceof Error) throw state.answer;
  return state.answer;
} }));

let root: Root; let node: HTMLDivElement; let client: QueryClient;
function Probe({ projectId, enabled = true }: { projectId: string; enabled?: boolean }) {
  return <span data-off={String(useAutoRetryOff(projectId, enabled))}/>;
}
async function off(projectId = "p", enabled = true): Promise<string | null> {
  await act(async () => { root.render(<QueryClientProvider client={client}><Probe projectId={projectId} enabled={enabled}/></QueryClientProvider>); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  return node.querySelector("span")!.getAttribute("data-off");
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  state.calls.length = 0; client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
});
afterEach(async () => { await act(async () => root.unmount()); client.clear(); node.remove(); });

it("is true only when the project's setting was read and is off, using the settings switch's query and RPC", async () => {
  state.answer = { value: { auto_retry_interrupted_model_call: false, settings_digest: "d" } };
  expect(await off()).toBe("true");
  expect(state.calls).toEqual([{ method: "model/callSettings/read", params: { project_id: "p" } }]);
  expect(client.getQueryData(["model-call-settings", "p"])).toBeDefined();
});

it("is false when the setting is on, and when it cannot be read", async () => {
  state.answer = { value: { auto_retry_interrupted_model_call: true, settings_digest: "d" } };
  expect(await off()).toBe("false");
  client.clear();
  state.answer = new Error("denied");
  expect(await off("q")).toBe("false");
  client.clear();
  state.answer = { value: {} };
  expect(await off("r")).toBe("false");
});

it("reads nothing for the example timeline or without a project", async () => {
  state.answer = { value: { auto_retry_interrupted_model_call: false, settings_digest: null } };
  expect(await off("p", false)).toBe("false");
  expect(await off("", true)).toBe("false");
  expect(state.calls).toEqual([]);
});

