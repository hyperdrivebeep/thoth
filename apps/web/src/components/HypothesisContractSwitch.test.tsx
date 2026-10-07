// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HypothesisContractSwitch } from "./HypothesisContractSwitch";

const state = vi.hoisted(() => ({ calls: [] as { method: string; input: Record<string, unknown> }[], on: false, fail: false }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    state.calls.push({ method, input });
    if (method === "model/callSettings/update") { if (state.fail) throw new Error("no"); state.on = input.hypothesis_contract_v3 === true; }
    return { value: { auto_retry_interrupted_model_call: false, hypothesis_contract_v3: state.on, settings_digest: "d".repeat(64) }, state: "SUCCEEDED", operation_id: "op" };
  } }));

let root: Root | undefined; let node: HTMLDivElement;
beforeEach(() => { state.calls = []; state.on = false; state.fail = false; });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); });
const settle = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise(done => setTimeout(done, 5)); }); };
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><HypothesisContractSwitch projectId="p"/></QueryClientProvider>));
  await settle();
}
const input = () => node.querySelector<HTMLInputElement>("input[type=checkbox]")!;

it("shows the switch off, says in easy words what it does and that it is off unless turned on", async () => {
  await mount();
  expect(input().checked).toBe(false);
  expect(node.textContent).toContain("가설 만들 때 예상 결과와 기각 조건도 함께 받기");
  expect(node.textContent).toContain("기본은 꺼짐");
  expect(node.textContent).toContain("모델을 더 부르지는 않습니다");
  expect(node.textContent).not.toMatch(/v3|hypothesis_portfolio|expected_by_hypothesis/);
});

it("sends only its own switch and the digest it read, and shows what the server says afterwards", async () => {
  await mount();
  await act(async () => input().click());
  await settle();
  const update = state.calls.find(call => call.method === "model/callSettings/update")!;
  expect(update.input).toEqual({ project_id: "p", hypothesis_contract_v3: true, expected_digest: "d".repeat(64) });
  expect(input().checked).toBe(true);
});

it("says so when the setting could not be changed", async () => {
  state.fail = true;
  await mount();
  await act(async () => input().click());
  await settle();
  expect(node.textContent).toContain("설정을 바꾸지 못했습니다");
  expect(input().checked).toBe(false);
});
