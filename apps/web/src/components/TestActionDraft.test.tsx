// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TestActionDraft } from "./TestActionDraft";

const calls = vi.hoisted(() => [] as { method: string; input: Record<string, unknown> }[]);
const reply = vi.hoisted(() => ({ action: { risk_tier: "R3", policy_state: "POLICY_UNDEFINED", proposal_state: "DRAFT" } as Record<string, unknown>, fail: "" }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    calls.push({ method, input });
    if (reply.fail) throw new Error(reply.fail);
    return { value: { action: { action_id: "a1", ...reply.action } }, state: "SUCCEEDED", operation_id: "op" };
  } }));

const BANNED = /승인됨|승인 완료|통과|해결|종결|면제/;
const LEAK = /POLICY_|DRAFT|HYPOTHESIS_BASIS|R4_|undefined|\bnull\b/;
let root: Root | undefined; let container: HTMLDivElement;
beforeEach(() => { calls.length = 0; reply.fail = ""; reply.action = { risk_tier: "R3", policy_state: "POLICY_UNDEFINED", proposal_state: "DRAFT" }; });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); });
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><TestActionDraft projectId="p" hypothesisId="h1" testId="t1"/></QueryClientProvider>));
}
const press = async () => {
  await act(async () => [...container.querySelectorAll("button")].find(item => item.textContent?.includes("행동 요청 준비"))!.click());
  await act(async () => { await new Promise(done => setTimeout(done, 0)); });
};

it("offers the button and says preparing a request is not an approval and asks no model", async () => {
  await mount();
  expect(container.textContent).toContain("이 시험으로 행동 요청 준비");
  expect(container.textContent).toContain("요청을 준비할 뿐 승인이 아닙니다");
  expect(container.textContent).toContain("모델을 부르지 않습니다");
  expect(calls).toHaveLength(0);
});

it("sends which test it is and an empty declaration, never a risk, then says the request is ready and still waits for an approval", async () => {
  await mount();
  await press();
  expect(calls).toEqual([{ method: "action/draft/fromTest",
    input: { project_id: "p", hypothesis_id: "h1", test_id: "t1", effect_declaration: { effect_completeness_confirmed: false } } }]);
  const text = container.textContent!;
  expect(text).toContain("행동 요청을 준비했습니다");
  expect(text).toContain("아직 승인이 아닙니다");
  expect(text).toContain("높음 · R3");
  expect(text).toContain("효과를 아직 선언하지 않아 보호된 행동으로 분류했습니다");
  expect(text).not.toMatch(BANNED);
  expect(text).not.toMatch(LEAK);
});

it("tells a declared protected action from a read-only one", async () => {
  reply.action = { risk_tier: "R3", policy_state: "APPROVAL_REQUIRED", proposal_state: "DRAFT" };
  await mount();
  await press();
  expect(container.textContent).toContain("밖으로 쓰거나 기준을 바꾸는 행동이라 사람의 승인이 필요합니다");
  reply.action = { risk_tier: "R0", policy_state: "AUTO_ALLOWED", proposal_state: "DRAFT" };
  await act(async () => root!.unmount()); container.remove(); root = undefined;
  await mount();
  await press();
  expect(container.textContent).toContain("위험 낮음 · R0");
  expect(container.textContent).not.toContain("사람의 승인이 필요합니다");
});

it("explains a refusal because the hypothesis stands on an old basis, and any other refusal in plain words", async () => {
  reply.fail = "HYPOTHESIS_BASIS_CHANGED";
  await mount();
  await press();
  expect(container.textContent).toContain("이 가설은 이전 근거 기준이라 행동 요청을 만들 수 없습니다");
  expect(container.textContent).not.toMatch(LEAK);
  reply.fail = "TEST_NOT_FOUND";
  await press();
  expect(container.textContent).toContain("행동 요청을 준비하지 못했습니다");
  expect(container.textContent).not.toMatch(LEAK);
});
const open = async () => act(async () => [...container.querySelectorAll("button")].find(item => item.textContent?.includes("이 행동이 하는 일 적기"))!.click());
const tick = (label: string) => [...container.querySelectorAll("label")].find(item => item.textContent?.includes(label))!.querySelector("input")!;
const choose = async (label: string) => act(async () => tick(label).click());

it("keeps the effect list folded and every box unchecked, and the model fills nothing in", async () => {
  await mount();
  await open();
  const boxes = [...container.querySelectorAll("input[type=checkbox]")] as HTMLInputElement[];
  expect(boxes.length).toBe(12);
  expect(boxes.every(box => !box.checked)).toBe(true);
  expect(container.textContent).toContain("외부 시스템에 씀");
  expect(container.textContent).toContain("이 행동이 하는 일을 빠짐없이 적었습니다");
  expect(container.textContent).not.toMatch(LEAK);
});

it("sends the one effect that was chosen as true and says the list is not complete", async () => {
  await mount();
  await open();
  await choose("면제를 내림");
  await press();
  expect(calls[0].input.effect_declaration).toEqual({ grants_waiver: true, effect_completeness_confirmed: false });
});

it("warns before sending when a forbidden effect is chosen, and still lets the request be made", async () => {
  await mount();
  await open();
  expect(container.textContent).not.toContain("실행할 수 없는 행동으로 분류됩니다");
  await choose("공식 성과 지표를 바꿈");
  expect(container.textContent).toContain("이 효과는 THOTH에서 실행할 수 없는 행동으로 분류됩니다");
  await press();
  expect(calls).toHaveLength(1);
});

it("names every effect as true or false when the person says the list is complete", async () => {
  await mount();
  await open();
  await choose("이 행동이 하는 일을 빠짐없이 적었습니다");
  await press();
  const declared = calls[0].input.effect_declaration as Record<string, boolean>;
  expect(Object.keys(declared)).toHaveLength(12);
  expect(Object.values(declared).filter(Boolean)).toHaveLength(1);
  expect(declared.effect_completeness_confirmed).toBe(true);
  expect(declared.external_write).toBe(false);
});

it("shows the rule's answer in plain words and does not work the risk out on its own", async () => {
  reply.action = { risk_tier: "R3", policy_state: "APPROVAL_REQUIRED", proposal_state: "DRAFT", required_roles: ["project-owner", "safety-owner"] };
  await mount();
  await open();
  await choose("외부 시스템에 씀");
  await press();
  const text = container.textContent!;
  expect(text).toContain("규칙상: 승인 필요 · 필요한 사람: 프로젝트 책임자, 안전 책임자");
  expect(text).not.toMatch(LEAK);
  expect(text).not.toMatch(/project-owner|safety-owner/);
  const result = [...container.querySelectorAll("[role=status]")].find(node => node.textContent?.includes("행동 요청을 준비했습니다"));
  expect(result).toBeDefined();
  expect(result!.textContent).not.toMatch(BANNED);
});

it("says who must look at an undeclared draft, and shows no person when the rule asks for none", async () => {
  reply.action = { risk_tier: "R3", policy_state: "POLICY_UNDEFINED", proposal_state: "DRAFT", required_roles: ["effect-owner"] };
  await mount();
  await press();
  expect(container.textContent).toContain("규칙상: 효과를 확인해야 함 · 필요한 사람: 효과 확인 책임자");
  reply.action = { risk_tier: "R0", policy_state: "AUTO_ALLOWED", proposal_state: "DRAFT", required_roles: [] };
  await act(async () => root!.unmount()); container.remove(); root = undefined;
  await mount();
  await press();
  expect(container.textContent).toContain("규칙상: 별도 승인 없이 가능");
  expect(container.textContent).not.toContain("필요한 사람");
});
