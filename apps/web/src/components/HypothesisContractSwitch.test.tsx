// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HypothesisContractSwitch } from "./HypothesisContractSwitch";
import { setLocale } from "../i18n/locale";

const state = vi.hoisted(() => ({ calls: [] as { method: string; input: Record<string, unknown> }[], on: true, fail: false, digest: null as string | null }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    state.calls.push({ method, input });
    if (method === "model/callSettings/update") { if (state.fail) throw new Error("no"); state.on = input.hypothesis_contract_v3 === true; state.digest = "d".repeat(64); }
    return { value: { auto_retry_interrupted_model_call: false, hypothesis_contract_v3: state.on, settings_digest: state.digest }, state: "SUCCEEDED", operation_id: "op" };
  } }));

let root: Root | undefined; let node: HTMLDivElement;
beforeEach(() => { state.calls = []; state.on = true; state.fail = false; state.digest = null; });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); setLocale("ko"); });
const settle = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise(done => setTimeout(done, 5)); }); };
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><HypothesisContractSwitch projectId="p"/></QueryClientProvider>));
  await settle();
}
const input = () => node.querySelector<HTMLInputElement>("input[type=checkbox]")!;

it("shows the unsaved server default on and explains saved choices and usage", async () => {
  await mount();
  expect(input().checked).toBe(true);
  expect(node.textContent).toContain("가설 만들 때 예상 결과와 기각 조건도 함께 받기");
  expect(node.textContent).toContain("저장된 선택이 없으면 기본으로 켜집니다");
  expect(node.textContent).toContain("이전에 끈 설정은 그대로 유지됩니다");
  expect(node.textContent).toContain("추가 모델 호출이 생길 수 있습니다");
  expect(node.textContent).not.toMatch(/v3|hypothesis_portfolio|expected_by_hypothesis/);
});

it("keeps a saved off choice visible without writing a new default", async () => {
  state.on = false; state.digest = "d".repeat(64);
  await mount();
  expect(input().checked).toBe(false);
  expect(state.calls.some(call => call.method === "model/callSettings/update")).toBe(false);
});

it("explains the same default and saved-off policy in English", async () => {
  setLocale("en");
  await mount();
  expect(input().checked).toBe(true);
  expect(node.textContent).toContain("On by default when no choice has been saved");
  expect(node.textContent).toContain("Saved off choices stay off");
});

it("sends only its own switch and the digest it read, and shows what the server says afterwards", async () => {
  state.on = false; state.digest = "d".repeat(64);
  await mount();
  await act(async () => input().click());
  await settle();
  const update = state.calls.find(call => call.method === "model/callSettings/update")!;
  expect(update.input).toEqual({ project_id: "p", hypothesis_contract_v3: true, expected_digest: "d".repeat(64) });
  expect(input().checked).toBe(true);
});

it("says so when the setting could not be changed", async () => {
  state.on = false; state.digest = "d".repeat(64);
  state.fail = true;
  await mount();
  await act(async () => input().click());
  await settle();
  expect(node.textContent).toContain("설정을 바꾸지 못했습니다");
  expect(input().checked).toBe(false);
});
