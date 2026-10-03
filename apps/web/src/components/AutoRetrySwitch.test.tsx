// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { AutoRetrySwitch, autoRetryHelp } from "./AutoRetrySwitch";

const state = vi.hoisted(() => ({ enabled: false, digest: null as string | null, calls: [] as { method: string; input: Record<string, unknown> }[], fail: false }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    state.calls.push({ method, input });
    if (state.fail) throw new Error("synthetic failure");
    if (method === "model/callSettings/update") { state.enabled = input.auto_retry_interrupted_model_call as boolean; state.digest = "s".repeat(64); }
    return { value: { auto_retry_interrupted_model_call: state.enabled, settings_digest: state.digest }, state: "SUCCEEDED", operation_id: "" };
  } }));

let root: Root | undefined; let container: HTMLDivElement;
const settle = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); }); };
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><AutoRetrySwitch projectId="project:p"/></QueryClientProvider>));
  await settle();
}
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); document.body.innerHTML = ""; state.enabled = false; state.digest = null; state.calls = []; state.fail = false; });

it("starts off as the server says, says what turning it on costs, and sends the digest it read when turned on", async () => {
  await mount();
  const input = container.querySelector<HTMLInputElement>("input[type=checkbox]")!;
  expect(input.checked).toBe(false);
  expect(container.textContent).toContain("모델 호출이 중간에 끊기면 그 호출만 최대 2번 자동으로 다시 시도합니다. 토큰이 더 쓰입니다.");
  expect(container.textContent).toContain("끄면 끊긴 결과 카드의 \"이어서 조사\"로 다시 할 수 있습니다.");
  expect(autoRetryHelp).toContain("토큰이 더 쓰입니다");
  expect(state.calls).toEqual([{ method: "model/callSettings/read", input: { project_id: "project:p" } }]);
  await act(async () => input.click());
  await settle();
  const update = state.calls.find(call => call.method === "model/callSettings/update")!;
  expect(update.input).toEqual({ project_id: "project:p", auto_retry_interrupted_model_call: true, expected_digest: null });
  expect(container.querySelector<HTMLInputElement>("input[type=checkbox]")!.checked).toBe(true);
});

it("says so when the setting cannot be read instead of showing it as off", async () => {
  state.fail = true;
  await mount();
  expect(container.textContent).toContain("자동 재시도 설정을 읽지 못했습니다");
  expect(container.querySelector("input[type=checkbox]")).toBeNull();
});

