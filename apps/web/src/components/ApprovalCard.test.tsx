// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ApprovalCard } from "./ApprovalCard";
import { ApprovalSection } from "./ApprovalSection";
import { parseApproval } from "./approvalView";

const state = vi.hoisted(() => ({ authorizations: [] as Record<string, unknown>[], planFails: false, calls: [] as { method: string; input: Record<string, unknown> }[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    state.calls.push({ method, input });
    if (method === "action/plan/read") {
      if (state.planFails) throw new Error("synthetic plan failure");
      return { value: { plan: { plan_id: input.plan_id, revision_digest: "e".repeat(64) }, authorizations: state.authorizations }, state: "SUCCEEDED", operation_id: "" };
    }
    if (method === "action/authorization/read") {
      const item = state.authorizations.find(entry => entry.authorization_id === input.authorization_id)!;
      return { value: { authorization: item, effective_state: item.state, payload_current: item.state === "APPROVED", review_needed: false }, state: "SUCCEEDED", operation_id: "" };
    }
    if (method === "action/authorization/prepare") {
      state.authorizations = [...state.authorizations, { ...state.authorizations[0], authorization_id: "auth:2", state: "PENDING", stale_reason: null, material_changes: [],
        decision_history: [], created_at: "2026-09-30T02:00:00Z" }];
    }
    return { value: {}, state: "SUCCEEDED", operation_id: "" };
  } }));

let root: Root | undefined; let node: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; node?.remove(); state.authorizations = []; state.planFails = false; state.calls = []; });
async function mount(element: React.ReactElement) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>));
  for (let i = 0; i < 8; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); });
}
const payload = { action_kind: "COMMUNICATION_SUBMISSION", channel: "EMAIL", content: { body_text: "안녕하세요 담당자님 시험 성적서를 보내 주세요", attachments: [{ name: "요청서.pdf", digest: "1".repeat(64) }] },
  targets: ["b".repeat(64)], disclosed_data: ["프로젝트 이름"], execution_roles: ["project-owner"], risk_tier: "R3", reversibility: "IRREVERSIBLE" };
const record = (patch: Record<string, unknown> = {}) => ({ authorization_id: "auth:1", step_id: "step:send", state: "APPROVED", stale_reason: null, plan_revision_digest: "a".repeat(64),
  predecessor_output_digests: [], target_baseline_digests: ["b".repeat(64)], policy_version: "policy:current",
  payload, material_changes: [], decision_history: [{ decision: "APPROVE", decided_at: "2026-09-30T00:00:00Z" }], schema_version: "2.0.0", created_at: "2026-09-30T00:00:00Z", ...patch });
const bodyChange = { field: "body", label: "본문", before: "", after: "", attachments: [],
  words: [{ op: "keep", text: "안녕하세요" }, { op: "keep", text: "담당자님" }, { op: "keep", text: "시험" }, { op: "del", text: "성적서를" }, { op: "add", text: "성적서와 원자료를" },
    { op: "keep", text: "보내" }, { op: "keep", text: "주세요" }, { op: "keep", text: "빨리" }, { op: "keep", text: "부탁드립니다" }] };
const attachmentChange = { field: "attachments", label: "첨부", before: "요청서.pdf", after: "요청서-v2.pdf", words: [],
  attachments: [{ before_name: "요청서.pdf", before_digest: "1".repeat(64), after_name: "요청서-v2.pdf", after_digest: "2".repeat(64) }] };
const stale = (changes: unknown[]) => record({ state: "STALE", stale_reason: "MATERIAL_CHANGE", material_changes: changes,
  decision_history: [{ decision: "APPROVE", decided_at: "2026-09-30T00:00:00Z" }, { decision: "STALE", stale_at: "2026-09-30T01:00:00Z" }] });
const view = (item: Record<string, unknown>, extra: Record<string, unknown> = {}) => parseApproval({ authorization: item, effective_state: item.state, payload_current: item.state === "APPROVED", review_needed: false, ...extra });

it("puts the changed parts first: what moved, and only the changed words with a little context", async () => {
  await mount(<ApprovalCard approval={view(stale([bodyChange, attachmentChange]))}/>);
  const text = node.textContent ?? "";
  expect(text).toContain("승인 뒤 바뀜: 본문 · 첨부");
  expect(node.querySelector("del")?.textContent).toBe("성적서를");
  expect(node.querySelector("ins")?.textContent).toBe("성적서와 원자료를");
  expect(text).toContain("…");
  expect(text).not.toContain("안녕하세요");
  expect(text).toContain("요청서.pdf"); expect(text).toContain("요청서-v2.pdf");
  expect(text).toContain("11111111"); expect(text).toContain("22222222");
  expect(text).not.toMatch(/MATERIAL_CHANGE|attachments|"body"/);
});

it("keeps the earlier approval as history of the version it covered and keeps the full content folded", async () => {
  await mount(<ApprovalCard approval={view(stale([bodyChange]))}/>);
  expect(node.textContent).toContain("이전 승인은 바뀌기 전 버전에 유효했음");
  const toggle = [...node.querySelectorAll("button")].find(b => b.textContent === "전체 내용 보기") as HTMLButtonElement;
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  await act(async () => toggle.click());
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(node.textContent).toContain("안녕하세요 담당자님 시험 성적서를 보내 주세요");
});

it("offers re-approval and not-sending only for a stale approval", async () => {
  const calls: string[] = [];
  await mount(<ApprovalCard approval={view(stale([bodyChange]))} onReapprove={() => calls.push("again")} onDecline={() => calls.push("no")}/>);
  const buttons = [...node.querySelectorAll("button")].map(b => b.textContent);
  expect(buttons).toContain("바뀐 내용으로 승인 요청 다시 만들기"); expect(buttons).toContain("보내지 않음");
  expect(buttons).not.toContain("바뀐 내용 확인 후 다시 승인");
  for (const label of ["바뀐 내용으로 승인 요청 다시 만들기", "보내지 않음"]) await act(async () => ([...node.querySelectorAll("button")].find(b => b.textContent === label) as HTMLButtonElement).click());
  expect(calls).toEqual(["again", "no"]);
  await act(async () => root!.unmount()); node.remove();
  await mount(<ApprovalCard approval={view(record())} onReapprove={() => undefined} onDecline={() => undefined}/>);
  expect([...node.querySelectorAll("button")].map(b => b.textContent)).not.toContain("보내지 않음");
  expect(node.textContent).toContain("승인됨");
});

it("says a look is advised when only the evidence moved, without voiding the approval", async () => {
  await mount(<ApprovalCard approval={view(record(), { review_needed: true })}/>);
  expect(node.textContent).toContain("승인됨"); expect(node.textContent).toContain("근거가 바뀌어 다시 살펴보는 것이 좋습니다");
  expect(node.textContent).not.toContain("승인 뒤 바뀜");
});

it("re-approval prepares a new approval for the current plan revision with the same digests and policy", async () => {
  state.authorizations = [stale([bodyChange])];
  await mount(<ApprovalSection projectId="project:p" planId="plan:1"/>);
  await act(async () => ([...node.querySelectorAll("button")].find(b => b.textContent === "바뀐 내용으로 승인 요청 다시 만들기") as HTMLButtonElement).click());
  for (let i = 0; i < 8; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); });
  expect(node.textContent).toContain("승인 대기");
  expect(node.textContent).toContain("승인 요청을 만들었습니다. 승인자가 결정하기 전까지 아무것도 보내지 않습니다");
  expect(node.textContent).not.toContain("승인됨");
  const prepared = state.calls.find(call => call.method === "action/authorization/prepare")!;
  expect(prepared.input).toEqual({ project_id: "project:p", plan_id: "plan:1", step_id: "step:send", plan_revision_digest: "e".repeat(64),
    predecessor_output_digests: [], target_baseline_digests: ["b".repeat(64)], policy_version: "policy:current" });
});

it("marks a change as not sent on this screen only, without any server call that sends", async () => {
  state.authorizations = [stale([bodyChange])];
  await mount(<ApprovalSection projectId="project:p" planId="plan:1"/>);
  await act(async () => ([...node.querySelectorAll("button")].find(b => b.textContent === "보내지 않음") as HTMLButtonElement).click());
  expect(node.querySelector(".approval-card")).toBeNull();
  expect(node.textContent).toContain("이 화면에만 적용");
  expect(state.calls.map(call => call.method).filter(method => !["action/plan/read", "action/authorization/read"].includes(method))).toEqual([]);
});

it("reads the plan's approvals and shows the newest state per step, or nothing when the plan cannot be read", async () => {
  state.authorizations = [stale([bodyChange])];
  await mount(<ApprovalSection projectId="project:p" planId="plan:1" onReapprove={() => undefined}/>);
  expect(state.calls[0]).toEqual({ method: "action/plan/read", input: { project_id: "project:p", plan_id: "plan:1" } });
  expect(node.textContent).toContain("승인 뒤 바뀜: 본문");
  await act(async () => root!.unmount()); node.remove(); state.planFails = true;
  await mount(<ApprovalSection projectId="project:p" planId="plan:1"/>);
  expect(node.textContent).toBe("");
});
