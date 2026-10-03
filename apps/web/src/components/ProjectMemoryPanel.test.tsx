// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ProjectMemoryPanel } from "./ProjectMemoryPanel";

const state = vi.hoisted(() => ({ revisions: [] as Record<string, unknown>[], fail: false, calls: [] as { method: string; input: Record<string, unknown> }[],
  transition: "COMMIT", support: "SUPPORTED", editError: null as string | null, injection: true, digest: null as string | null }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>) => {
    state.calls.push({ method, input });
    if (method === "memory/settings/read") return { value: { memory_injection: state.injection, settings_digest: state.digest }, state: "SUCCEEDED", operation_id: "" };
    if (method === "memory/settings/update") {
      state.injection = input.memory_injection as boolean; state.digest = "s".repeat(64);
      return { value: { memory_injection: state.injection, settings_digest: state.digest }, state: "SUCCEEDED", operation_id: "" };
    }
    if (method === "memory/edit/propose") {
      if (state.editError) { const { RpcError } = await importOriginal<typeof import("../api/rpcClient")>(); throw new RpcError(state.editError, -32030, {}); }
      return { value: { edit_id: "memory-edit:1", transition: state.transition, revision: { support_status: state.support }, reviews: [] }, state: "SUCCEEDED", operation_id: "" };
    }
    if (state.fail) throw new Error("synthetic read failure");
    const recallable = state.revisions.filter(item => item.not_recalled_because === null).length;
    return { value: { revisions: state.revisions, counts: { total: state.revisions.length, recallable, not_recalled: state.revisions.length - recallable } }, state: "SUCCEEDED", operation_id: "" };
  } }));

let root: Root | undefined; let container: HTMLDivElement;
const row = (patch: Record<string, unknown> = {}) => ({ memory_id: "memory:abc", memory_revision_id: "rev:abc", revision_digest: "d".repeat(64), parent_revision_digest: null,
  owner_revision_ref: "revision:owner:1", origin_thread_id: "thread:1", kind: "LESSON", summary: "표본이 작으면 결론을 보류한다", assertion: "표본이 작으면 결론을 보류한다", source_ref: null, content_excerpt: "",
  evidence_count: 2, evidence_refs: [], transition: "COMMIT", support_status: "SUPPORTED", authority_status: "AUTHORITATIVE", cutoff_valid: true, recall_eligible: true, action_eligible: false,
  owner_is_current: true, is_latest: true, not_recalled_because: null, created_at: "2026-09-30T00:00:00Z", ...patch });
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><ProjectMemoryPanel projectId="project:p"/></QueryClientProvider>));
  for (let i = 0; i < 20 && !container.querySelector("[data-memory-ready], [role=alert]"); i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); });
}
const visible = () => container.innerHTML.replace(/<details[\s\S]*?<\/details>/g, "").replace(/ title="[^"]*"/g, "");
const settle = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(r => setTimeout(r, 10)); }); };
const click = async (element: Element | null) => { await act(async () => (element as HTMLElement).click()); await settle(); };
const dialog = () => document.body.querySelector(".bp6-dialog");
const buttonByText = (scope: ParentNode, text: string) => [...scope.querySelectorAll("button")].find(b => b.textContent?.trim() === text) ?? null;
const typeInto = async (label: string, value: string) => act(async () => {
  const field = dialog()!.querySelector<HTMLTextAreaElement>(`textarea[aria-label='${label}']`)!;
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
});
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; container?.remove(); document.body.innerHTML = ""; state.revisions = []; state.fail = false; state.calls = []; state.transition = "COMMIT"; state.editError = null; });
afterEach(() => { state.support = "SUPPORTED"; state.injection = true; state.digest = null; });

it("reads the memory version list and says plainly when it holds no stored record", async () => {
  await mount();
  expect(state.calls.filter(call => call.method === "memory/revision/list")).toEqual([{ method: "memory/revision/list", input: { project_id: "project:p" } }]);
  expect(container.textContent).toContain("이 목록에 저장된 기억 기록이 없습니다");
  expect(container.textContent).not.toContain("다음 조사에 불러올");
  expect(container.textContent).toContain("실제 조사에서 어떤 기억을 불러올지는 질문마다 달라서 이 화면에서 정하지 않습니다");
});

it("shows each version's review result, evidence, source and time state in Korean and keeps ids under technical details", async () => {
  state.revisions = [row()];
  await mount();
  const html = visible();
  for (const text of ["교훈", "반영됨", "근거 충분", "공식 출처", "기준시점 적합", "현재 버전", "표본이 작으면 결론을 보류한다"]) expect(html).toContain(text);
  expect(container.textContent).toContain("저장된 기억 기록 1개");
  expect(container.textContent).toContain("질문에 맞으면 불러올 수 있는 기록 1개");
  for (const raw of ["LESSON", "COMMIT", "SUPPORTED", "AUTHORITATIVE"]) expect(html).not.toContain(raw);
  expect(html).not.toContain("d".repeat(64)); expect(html).not.toContain("memory:abc");
  expect(container.querySelector("details")?.textContent).toContain("d".repeat(64));
});

it("says why a version would not be recalled, and marks an older version as superseded", async () => {
  state.revisions = [
    row({ memory_revision_id: "rev:old", is_latest: false, not_recalled_because: "SUPERSEDED_BY_NEWER_VERSION" }),
    row({ memory_revision_id: "rev:hold", transition: "HOLD", recall_eligible: false, not_recalled_because: "TRANSITION_HOLD" }),
    row({ memory_revision_id: "rev:quarantine", transition: "QUARANTINE", recall_eligible: false, not_recalled_because: "TRANSITION_QUARANTINE" }),
    row({ memory_revision_id: "rev:owner", not_recalled_because: "OWNER_REVISION_NOT_CURRENT", owner_is_current: false }),
    row({ memory_revision_id: "rev:new", not_recalled_because: "SOMETHING_NEW" }),
    row({ memory_revision_id: "rev:bare", not_recalled_because: "AUTO_MEMORY_NO_EVIDENCE" }),
  ];
  await mount();
  const text = container.textContent ?? "";
  expect(text).toContain("새 버전으로 대체됨");
  for (const label of ["더 새로운 버전이 있음", "검토에서 보류됨", "검토에서 격리됨", "원본이 바뀌어 다시 확인이 필요함"]) expect(text).toContain(label);
  expect(text).toContain("SOMETHING_NEW");
  expect(text).toContain("근거 자료가 없는 자동 기억");
  expect(text).toContain("불러오지 않는 기록 6개");
  expect(text).not.toMatch(/TRANSITION_|OWNER_REVISION|SUPERSEDED_BY|AUTO_MEMORY_/);
});

it("offers a correction on the current version only, and labels the replaced original and the user correction", async () => {
  state.revisions = [
    row({ memory_revision_id: "rev:old", is_latest: false, not_recalled_because: "SUPERSEDED_BY_NEWER_VERSION" }),
    row({ memory_revision_id: "rev:new", revision_digest: "e".repeat(64), source_ref: "MEMORY:memory-edit:1", parent_revision_digest: "d".repeat(64), assertion: "표본이 작으면 결론을 보류하고 다시 확인한다", summary: "표본이 작으면 결론을 보류하고 다시 확인한다" }),
  ];
  await mount();
  expect([...container.querySelectorAll("button")].filter(b => b.textContent?.trim() === "수정 제안")).toHaveLength(1);
  const html = visible();
  expect(html).toContain("새 버전으로 대체됨"); expect(html).toContain("사용자 정정");
  expect(html).not.toContain("MEMORY:memory-edit"); expect(html).not.toContain("e".repeat(64));
});

it("opens the correction window with the original memory, needs both fields, and sends nothing until both are filled", async () => {
  state.revisions = [row()];
  await mount();
  await click(buttonByText(container, "수정 제안"));
  expect(dialog()?.textContent).toContain("표본이 작으면 결론을 보류한다");
  await click(buttonByText(document.body, "수정 제안 보내기"));
  expect(dialog()?.textContent).toContain("고친 내용과 이유를 모두 적어 주세요");
  expect(state.calls.some(call => call.method === "memory/edit/propose")).toBe(false);
  await click(buttonByText(document.body, "닫기"));
  expect(state.calls.some(call => call.method === "memory/edit/propose")).toBe(false);
});

it.each([
  ["COMMIT", "반영됨", "다음 조사부터 원래 기억 대신 이 정정을 불러올 수 있습니다."],
  ["REVISE", "수정 필요", "내용이 짧거나 구체적이지 않습니다."],
  ["HOLD", "보류", "관계가 불분명해 보류했습니다."],
  ["QUARANTINE", "격리", "명령문이나 비밀값으로 보이는 내용이 있어 저장만 하고 쓰지 않습니다."],
])("sends the correction and reads the result back as one line for %s", async (transition, label, line) => {
  state.revisions = [row({ evidence_refs: ["span:1", "span:2"] })]; state.transition = transition;
  await mount();
  await click(buttonByText(container, "수정 제안"));
  await typeInto("고친 내용", "표본이 작으면 결론을 보류하고 다시 확인한다");
  await typeInto("이유", "원래 기억이 현재 자료와 다릅니다");
  await click(buttonByText(document.body, "수정 제안 보내기"));
  const sent = state.calls.find(call => call.method === "memory/edit/propose")!;
  expect(sent.input).toEqual({ project_id: "project:p", target_revision_digest: "d".repeat(64), corrected_text: "표본이 작으면 결론을 보류하고 다시 확인한다",
    reason: "원래 기억이 현재 자료와 다릅니다", evidence_refs: [] });
  const status = container.querySelector("[data-memory-edit-result]")?.textContent ?? "";
  expect(status).toContain(label); expect(status).toContain(line);
  expect(state.calls.filter(call => call.method === "memory/revision/list").length).toBeGreaterThanOrEqual(2);
  for (let i = 0; i < 30 && dialog(); i++) await act(async () => { await new Promise(r => setTimeout(r, 20)); });
  expect(dialog()).toBeNull();
});

it("links the original evidence only when it is chosen", async () => {
  state.revisions = [row({ evidence_refs: ["span:1", "span:2"] })];
  await mount();
  await click(buttonByText(container, "수정 제안"));
  expect(dialog()?.textContent).toContain("원래 기억의 근거 2개를 함께 연결");
  await click(dialog()!.querySelector("input[type=checkbox]"));
  await typeInto("고친 내용", "표본이 작으면 결론을 보류한다 다시");
  await typeInto("이유", "근거가 같습니다");
  await click(buttonByText(document.body, "수정 제안 보내기"));
  expect(state.calls.find(call => call.method === "memory/edit/propose")!.input.evidence_refs).toEqual(["span:1", "span:2"]);
});

it("shows a referenced memory as a sentence and keeps its internal id out of the default view", async () => {
  state.revisions = [row({ assertion: null, source_ref: "HYPOTHESIS:hypothesis-a02-data", summary: "INPUT_MATERIAL_DATA may explain the result" })];
  await mount();
  expect(visible()).toContain("INPUT_MATERIAL_DATA may explain the result");
  expect(visible()).not.toContain("HYPOTHESIS:hypothesis-a02-data");
  await click(buttonByText(container, "수정 제안"));
  expect(dialog()?.textContent).toContain("원래 기억: INPUT_MATERIAL_DATA may explain the result");
  expect(dialog()?.textContent).not.toContain("HYPOTHESIS:hypothesis-a02-data");
});

it.each(["OWNER_REVISION_NOT_CURRENT", "DEPENDENCY_REVIEW_REQUIRED"])("turns the proposal off and says the original basis changed when the memory is not recalled because of %s", async reason => {
  state.revisions = [row({ not_recalled_because: reason, owner_is_current: reason !== "OWNER_REVISION_NOT_CURRENT" })];
  await mount();
  expect(buttonByText(container, "수정 제안")).toBeNull();
  expect(container.textContent).toContain("원래 근거가 바뀌었습니다");
});

it("shows the same sentence when the server refuses a correction whose basis changed meanwhile", async () => {
  state.revisions = [row()]; state.editError = "MEMORY_EDIT_TARGET_STALE";
  await mount();
  await click(buttonByText(container, "수정 제안"));
  await typeInto("고친 내용", "표본이 작으면 결론을 보류한다 다시");
  await typeInto("이유", "근거가 같습니다");
  await click(buttonByText(document.body, "수정 제안 보내기"));
  expect(document.body.textContent).toContain("원래 근거가 바뀌었습니다");
  expect(document.body.textContent).not.toContain("MEMORY_EDIT_TARGET_STALE");
});

it("shows a read failure instead of an empty memory", async () => {
  state.fail = true; await mount();
  expect(container.querySelector("[role=alert]")?.textContent).toContain("기억을 읽지 못했습니다");
  expect(container.textContent).not.toContain("기억 기록이 없습니다");
});

it("says which kind of hold a correction got when its value differs from an accepted memory of the same record", async () => {
  state.revisions = [row()]; state.transition = "HOLD"; state.support = "CONFLICTING";
  await mount();
  await click(buttonByText(container, "수정 제안"));
  await typeInto("고친 내용", "표본이 크면 결론을 확정한다 다시");
  await typeInto("이유", "다른 근거가 있습니다");
  await click(buttonByText(document.body, "수정 제안 보내기"));
  const status = container.querySelector("[data-memory-edit-result]")?.textContent ?? "";
  expect(status).toContain("같은 기록의 값이 이미 반영된 기억과 달라 보류했습니다.");
  expect(status).not.toContain("관계가 불분명");
});

it("shows the memory switch on, with its one-line description, and turns it off with the digest it read", async () => {
  await mount(); await settle();
  expect(container.textContent).toContain("조사에 기억 사용");
  expect(container.textContent).toContain("끄면 저장된 기억과 정정은 그대로 두고, 앞으로의 조사에는 기억을 넣지 않습니다.");
  const box = container.querySelector<HTMLInputElement>("[data-memory-switch] input[type=checkbox]")!;
  expect(box.checked).toBe(true);
  await click(box);
  const sent = state.calls.find(call => call.method === "memory/settings/update")!;
  expect(sent.input).toEqual({ project_id: "project:p", memory_injection: false, expected_digest: null });
  expect(container.querySelector<HTMLInputElement>("[data-memory-switch] input[type=checkbox]")!.checked).toBe(false);
  expect(state.calls.some(call => call.method === "memory/edit/propose")).toBe(false);
});

it("shows a memory list that is switched off as it is: nothing is hidden or deleted", async () => {
  state.injection = false; state.revisions = [row()];
  await mount(); await settle();
  expect(container.querySelector<HTMLInputElement>("[data-memory-switch] input[type=checkbox]")!.checked).toBe(false);
  expect(container.textContent).toContain("저장된 기억 기록 1개");
  expect(container.textContent).toContain("표본이 작으면 결론을 보류한다");
});
