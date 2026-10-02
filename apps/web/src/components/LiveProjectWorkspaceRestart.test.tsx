// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { readDraft, writeDraft } from "../api/conversation";
import { readWorkspaceContext, saveWorkspaceContext } from "../api/research";
import { readLocalPending, submissionSignature, writeLocalPending } from "../api/localWorkspacePersistence";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";

const ids = { A: `workspace:${"a".repeat(32)}`, B: `workspace:${"b".repeat(32)}` };
const fixture = vi.hoisted(() => ({ workspace: "A" as "A" | "B", executionReady: false, workspaceReadable: true, setupComplete: true,
  setupFailure: null as "CORRUPT" | "UNREADABLE" | null,
  projects: ["p"] as string[], threads: ["t"] as string[], failAdmission: false,
  admissionKind: "RUNNING" as "RUNNING" | "QUEUED" | "COMPLETED_REPLAY" | "MALFORMED",
  threadOperation: "SUCCEEDED" as "SUCCEEDED" | "RUNNING",
  oldResultGate: null as Promise<void> | null,
  projectListGate: null as Promise<void> | null,
  threadListGate: null as Promise<void> | null,
  admissionGate: null as Promise<void> | null,
  lostResponseOnce: false, replayTerminal: false, createdOperations: 0, operationByKey: new Map<string, string>(),
  calls: [] as { method: string; input: Record<string, unknown>; workspace: "A" | "B"; key: string }[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>, key: string) => {
    const currentWorkspace = fixture.workspace;
    fixture.calls.push({ method, input, workspace: currentWorkspace, key });
    const workspaceId = currentWorkspace === "A" ? `workspace:${"a".repeat(32)}` : `workspace:${"b".repeat(32)}`;
    let value: unknown = {};
    if (method === "workspace/ready") value = fixture.setupFailure
      ? { setup_status: fixture.setupFailure, reason_code: `WORKSPACE_SETUP_${fixture.setupFailure}`,
        workspace_readable: true, execution_ready: false, ready: false, setup: null,
        setup_complete: false, model_connected: false, workspace_id: workspaceId }
      : { ready: fixture.executionReady, execution_ready: fixture.executionReady,
        workspace_readable: fixture.workspaceReadable, setup_complete: fixture.setupComplete, workspace_id: workspaceId, deployment_mode: "LOCAL",
        model_connected: fixture.executionReady, setup: { internet_consent: fixture.setupComplete ? "DENIED" : "UNDECIDED" } };
    if (method === "project/list") {
      if (fixture.projectListGate) await fixture.projectListGate;
      value = { projects: fixture.projects.map(project_id =>
        ({ project_id, name: `${currentWorkspace} synthetic project`, overlay: "test", lifecycle: "ACTIVE", revision: 1, cutoff_at: "2026-09-26T00:00:00Z" })) };
    }
    if (method === "thread/list" && fixture.threadListGate) await fixture.threadListGate;
    if (method === "thread/list") value = { threads: fixture.threads.map((thread_id, index) => ({ project_id: input.project_id, thread_id,
      updated_at: `2026-09-2${index}T00:00:00Z`, problem: `${currentWorkspace} stored work`, execution_state: "IDLE", lifecycle: "OPEN" })) };
    if (method === "thread/read") value = { project_id: input.project_id, thread_id: input.thread_id,
      problem: `${currentWorkspace} stored work`, lifecycle: "OPEN", execution_state: "IDLE", current_object_ids: [], operation_state: fixture.threadOperation, current_result: null };
    if (method === "thread/activity/list") value = { conversation: { turns: [{ request_epoch: 1, request_revision_digest: "1".repeat(64),
      operation_id: "op-stored", text: "synthetic question", edit_kind: "INITIAL", created_at: "2026-09-26T00:00:00Z",
      authored_text_ref: { revision_digest: "1".repeat(64) } }], next_before_epoch: null, history_limited: false } };
    if (method === "operation/result/read") {
      if (currentWorkspace === "A" && fixture.oldResultGate) await fixture.oldResultGate;
      value = { operation_id: "op-stored", state: "SUCCEEDED",
        result: { answer: `${currentWorkspace} stored synthetic answer`, thread_id: "t", request_epoch: 1 }, error: null };
    }
    if (method === "project/source/list") value = { artifacts: [] };
    if (method === "evidence/list") value = { evidence: [] };
    if (method === "model/settings/read") value = { settings_digest: null, selection: { provider: null, model: null, reasoning_effort: null },
      effective_settings: null, availability: "UNAVAILABLE", reason_code: "MODEL_CAPABILITY_UNKNOWN", model_options: [] };
    if (method === "model/credential/list") value = { accounts: [] };
    if (method === "project/create") {
      const projectId = String(input.project_id);
      fixture.projects.push(projectId);
      value = { project_id: projectId, name: input.name, overlay: input.overlay, lifecycle: "DRAFT", revision: 1 };
    }
    if (method === "thread/input") {
      if (fixture.failAdmission) throw new Error("synthetic admission failure");
      if (fixture.admissionGate) await fixture.admissionGate;
      value = fixture.admissionKind === "QUEUED"
        ? { contract_version: 2, project_id: "p", thread_id: "t", operation_id: "op-queued", status: "QUEUED_AFTER_CURRENT" }
        : fixture.admissionKind === "COMPLETED_REPLAY"
          ? { contract_version: 2, thread_id: "t", request_epoch: 2, terminal_reason: "BOUNDED_RESEARCH_COMPLETE", answer: "synthetic completed result" }
        : fixture.admissionKind === "MALFORMED"
          ? { contract_version: 2, project_id: "p", thread_id: "t", operation_id: "op-new", status: "HOLD", request_epoch: 2 }
          : { contract_version: 2, project_id: "p", thread_id: "t", operation_id: "op-new", status: "ACCEPTED_RUNNING", request_epoch: 2 };
    }
    if (method === "thread/start") {
      if (!fixture.operationByKey.has(key)) { fixture.operationByKey.set(key, "op-first"); fixture.createdOperations += 1; }
      if (fixture.lostResponseOnce) { fixture.lostResponseOnce = false; throw new TypeError("synthetic accepted response lost"); }
      value = fixture.replayTerminal
        ? { contract_version: 2, thread_id: "t", request_epoch: 1, terminal_reason: "BOUNDED_RESEARCH_COMPLETE" }
        : { contract_version: 2, project_id: "p", thread_id: "t", operation_id: "op-first", status: "ACCEPTED_RUNNING", request_epoch: 1 };
    }
    return { value, state: method === "thread/start" ? fixture.replayTerminal ? "SUCCEEDED" : "RUNNING"
      : method === "thread/input" && fixture.admissionKind !== "COMPLETED_REPLAY" ? "RUNNING" : "SUCCEEDED",
      operation_id: method === "thread/start" ? fixture.operationByKey.get(key) : method === "thread/input" ? fixture.admissionKind === "QUEUED" ? "op-queued" : "op-new" : "op" };
  },
}));

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;
const scope = (workspace: "A" | "B") => ({ mode: "LOCAL" as const, workspaceId: ids[workspace] });
async function settle(check: () => boolean) {
  for (let i = 0; i < 25; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
    if (check()) return;
  }
  throw new Error(`workspace did not settle: ${container.textContent?.slice(0, 220)}`);
}
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "synthetic" }))));
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => root!.render(<QueryClientProvider client={client}><LiveProjectWorkspace /></QueryClientProvider>));
}
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; client?.clear(); container?.remove(); vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals();
  fixture.workspace = "A"; fixture.executionReady = false; fixture.workspaceReadable = true; fixture.setupComplete = true;
  fixture.setupFailure = null;
  fixture.projects = ["p"]; fixture.threads = ["t"];
  fixture.failAdmission = false; fixture.admissionKind = "RUNNING"; fixture.threadOperation = "SUCCEEDED"; fixture.oldResultGate = null;
  fixture.projectListGate = null; fixture.threadListGate = null; fixture.admissionGate = null; fixture.lostResponseOnce = false; fixture.replayTerminal = false;
  fixture.createdOperations = 0; fixture.operationByKey.clear(); fixture.calls = [];
});

it("opens authorized stored research and draft while disconnected, but cannot submit a new request", async () => {
  saveWorkspaceContext("p", "t", scope("A"));
  writeDraft("p", "t", "unsubmitted A draft", scope("A"));
  await mount();
  await settle(() => Boolean(container.textContent?.includes("A stored synthetic answer")));
  expect((container.querySelector("#live-problem") as HTMLTextAreaElement).value).toBe("unsubmitted A draft");
  expect(container.textContent).toContain("저장된 연구는 계속 볼 수 있습니다");
  expect((container.querySelector('[aria-label="지시 추가"]') as HTMLButtonElement).disabled).toBe(true);
  const recordsTab = [...container.querySelectorAll('[role="tab"]')].find(tab => tab.textContent === "연구 이력");
  expect(recordsTab?.getAttribute("aria-disabled")).not.toBe("true");
  await act(async () => container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  expect(fixture.calls.some(call => call.method === "thread/input")).toBe(false);
  const reconnect = [...container.querySelectorAll("button")].find(button => button.textContent === "모델 연결 확인")!;
  await act(async () => reconnect.click());
  await settle(() => Boolean(container.textContent?.includes("계정 연결")));
  expect(fixture.calls.some(call => call.method === "model/credential/register")).toBe(false);
});

it("keeps authorized research readable when the LOCAL setup record is corrupt", async () => {
  fixture.setupFailure = "CORRUPT";
  saveWorkspaceContext("p", "t", scope("A"));
  await mount();
  await settle(() => Boolean(container.textContent?.includes("A stored synthetic answer")));
  expect(container.textContent).toContain("설정 파일이 손상");
  expect((container.querySelector('[aria-label="지시 추가"]') as HTMLButtonElement).disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "thread/read")).toBe(true);
  expect(fixture.calls.some(call => call.method === "workspace/setup/update" || call.method === "model/credential/register" || call.method === "thread/input")).toBe(false);
});

it("shows unreadable LOCAL setup without offering a fresh consent write on first entry", async () => {
  fixture.setupFailure = "UNREADABLE";
  fixture.projects = [];
  await mount();
  await settle(() => fixture.calls.some(call => call.method === "project/list") && Boolean(container.querySelector(".first-run-card")));
  expect(container.textContent).toContain("설정 파일을 읽지 못");
  expect(container.textContent).not.toContain("공개 웹을 이 워크스페이스에서 쓸까요");
  expect(fixture.calls.some(call => call.method === "workspace/setup/update" || call.method === "model/credential/register")).toBe(false);
});

it("keeps the parent as the sole readiness query owner while opening project settings", async () => {
  saveWorkspaceContext("p", "t", scope("A"));
  await mount();
  await settle(() => Boolean(container.textContent?.includes("A stored synthetic answer")));
  const readyBefore = fixture.calls.filter(call => call.method === "workspace/ready").length;
  await act(async () => { container.querySelector<HTMLElement>('[role="tab"][data-tab-id="settings"]')!.click(); });
  await settle(() => Boolean(container.textContent?.includes("모델 기본값")));
  expect(fixture.calls.filter(call => call.method === "workspace/ready")).toHaveLength(readyBefore);
  expect(container.textContent).not.toContain("저장된 작업의 접근 범위를 다시 확인하는 중");
});

it("settles after project creation followed by an immediate settings visit", async () => {
  fixture.projects = [];
  await mount();
  await settle(() => Boolean(container.querySelector('input[placeholder="연구할 문제 또는 프로젝트 이름"]')));
  const input = container.querySelector<HTMLInputElement>('input[placeholder="연구할 문제 또는 프로젝트 이름"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "new synthetic project");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => { container.querySelector("form.onboarding-card")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  await settle(() => fixture.calls.some(call => call.method === "project/create"));
  await settle(() => Boolean(container.querySelector(".workspace-navigation")));
  const readyBefore = fixture.calls.filter(call => call.method === "workspace/ready").length;
  await act(async () => { container.querySelector<HTMLElement>('[role="tab"][data-tab-id="settings"]')!.click(); });
  await settle(() => Boolean(container.textContent?.includes("모델 기본값")));
  expect(fixture.calls.filter(call => call.method === "workspace/ready")).toHaveLength(readyBefore);
  expect(container.textContent).not.toContain("저장된 작업의 접근 범위를 다시 확인하는 중");
});

it("keeps an existing authorized project readable when setup is unfinished, without enabling execution", async () => {
  fixture.setupComplete = false;
  saveWorkspaceContext("p", "t", scope("A"));
  await mount(); await settle(() => Boolean(container.textContent?.includes("A stored synthetic answer")));
  expect((container.querySelector('[aria-label="지시 추가"]') as HTMLButtonElement).disabled).toBe(true);
  expect(container.querySelector(".first-run-shell")).toBeNull();
});

it("does not open saved research if LOCAL workspace readability is explicitly false", async () => {
  fixture.workspaceReadable = false;
  saveWorkspaceContext("p", "t", scope("A"));
  await mount(); await settle(() => Boolean(container.textContent?.includes("저장된 연구를 지금 읽을 수 없습니다")));
  expect(container.textContent).not.toContain("A stored synthetic answer");
  expect(fixture.calls.some(call => call.method === "thread/read")).toBe(false);
});

it("never borrows a same-named project, result or draft from another LOCAL workspace", async () => {
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "A draft", scope("A"));
  saveWorkspaceContext("p", "t", scope("B")); writeDraft("p", "t", "B draft", scope("B"));
  await mount(); await settle(() => Boolean(container.textContent?.includes("A stored synthetic answer")));
  fixture.workspace = "B";
  await act(async () => { await client.invalidateQueries({ queryKey: ["workspace-ready"] }); });
  await settle(() => Boolean(container.textContent?.includes("B stored synthetic answer")));
  expect((container.querySelector("#live-problem") as HTMLTextAreaElement).value).toBe("B draft");
  expect(container.textContent).not.toContain("A stored synthetic answer");
  expect(readWorkspaceContext(scope("A"))).toEqual({ projectId: "p", threadId: "t" });
});

it("does not render a late result from the previous workspace after switching roots", async () => {
  let releaseOld!: () => void;
  fixture.oldResultGate = new Promise<void>(resolve => { releaseOld = resolve; });
  saveWorkspaceContext("p", "t", scope("A")); saveWorkspaceContext("p", "t", scope("B"));
  await mount();
  try {
    await settle(() => fixture.calls.some(call => call.workspace === "A" && call.method === "operation/result/read"));
    fixture.workspace = "B";
    await act(async () => { await client.invalidateQueries({ queryKey: ["workspace-ready"] }); });
    await settle(() => Boolean(container.textContent?.includes("B stored synthetic answer")));
    releaseOld(); await settle(() => Boolean(container.textContent?.includes("B stored synthetic answer")));
    expect(container.textContent).not.toContain("A stored synthetic answer");
  } finally { releaseOld(); }
});

it("drops an unauthorized saved selection without deleting its unsent draft", async () => {
  saveWorkspaceContext("missing-project", "t", scope("A"));
  writeDraft("missing-project", "t", "private unsent draft", scope("A"));
  await mount(); await settle(() => Boolean(container.textContent?.includes("저장된 프로젝트 선택 확인 필요")));
  expect(readWorkspaceContext(scope("A"))).toEqual({ projectId: "", threadId: "" });
  expect(readDraft("missing-project", "t", scope("A"))).toBe("private unsent draft");
  expect(fixture.calls.some(call => call.method === "thread/read" && call.input.thread_id === "t")).toBe(false);
});

it("preserves a corrupt LOCAL context until the user explicitly selects a project", async () => {
  const key = `thoth:local-workspace:v1:context:${ids.A}`;
  localStorage.setItem(key, "{corrupt-context");
  await mount();
  await settle(() => Boolean(container.querySelector(".project-home-item")));
  expect(container.textContent).toContain("마지막 작업 위치 기록을 읽지 못했습니다");
  expect(localStorage.getItem(key)).toBe("{corrupt-context");
  expect(fixture.calls.some(call => call.method === "thread/read" || call.method === "thread/input" || call.method === "thread/start")).toBe(false);
  await act(async () => { container.querySelector<HTMLButtonElement>(".project-home-item")!.click(); });
  // A8: explicitly opening a project lands on its most recently updated work.
  await settle(() => readWorkspaceContext(scope("A")).threadId === "t");
  expect(readWorkspaceContext(scope("A"))).toEqual({ projectId: "p", threadId: "t" });
});

it("removes displayed research after the server withdraws project access", async () => {
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "private draft", scope("A"));
  await mount(); await settle(() => Boolean(container.textContent?.includes("A stored synthetic answer")));
  fixture.projects = [];
  await act(async () => { await client.invalidateQueries({ queryKey: ["projects"] }); });
  await settle(() => Boolean(container.textContent?.includes("저장된 프로젝트 선택 확인 필요")));
  expect(container.textContent).not.toContain("A stored synthetic answer");
  expect(readWorkspaceContext(scope("A"))).toEqual({ projectId: "", threadId: "" });
  expect(readDraft("p", "t", scope("A"))).toBe("private draft");
  expect(client.getQueriesData({ queryKey: ["research"] }).some(([key]) => key.includes("p"))).toBe(false);
});

it("hides cached research during a permission refetch and clears it after denial", async () => {
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "preserved draft", scope("A"));
  await mount(); await settle(() => Boolean(container.textContent?.includes("A stored synthetic answer")));
  let release!: () => void;
  fixture.projectListGate = new Promise<void>(resolve => { release = resolve; });
  fixture.projects = [];
  try {
    await act(async () => { void client.invalidateQueries({ queryKey: ["projects"] }); });
    await settle(() => Boolean(container.textContent?.includes("접근 범위를 다시 확인하는 중")));
    expect(container.textContent).not.toContain("A stored synthetic answer");
    release();
    await settle(() => Boolean(container.textContent?.includes("저장된 프로젝트 선택 확인 필요")));
    expect(readDraft("p", "t", scope("A"))).toBe("preserved draft");
  } finally { release(); }
});

it("does not apply a late admission after project access is withdrawn", async () => {
  fixture.executionReady = true;
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "unsent on revoke", scope("A"));
  await mount(); await settle(() => Boolean(container.querySelector<HTMLButtonElement>('[aria-label="지시 추가"]:not([disabled])')));
  let release!: () => void;
  fixture.admissionGate = new Promise<void>(resolve => { release = resolve; });
  try {
    await act(async () => container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    await settle(() => fixture.calls.some(call => call.method === "thread/input"));
    fixture.projects = [];
    await act(async () => { await client.invalidateQueries({ queryKey: ["projects"] }); });
    await settle(() => Boolean(container.textContent?.includes("저장된 프로젝트 선택 확인 필요")));
    release(); await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(readWorkspaceContext(scope("A"))).toEqual({ projectId: "", threadId: "" });
    expect(readDraft("p", "t", scope("A"))).toBe("unsent on revoke");
  } finally { release(); }
});

it("keeps an editable draft through same-workspace background permission refetch", async () => {
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "existing draft", scope("A"));
  await mount(); await settle(() => Boolean(container.textContent?.includes("A stored synthetic answer")));
  let release!: () => void;
  fixture.projectListGate = new Promise<void>(resolve => { release = resolve; });
  try {
    await act(async () => { void client.invalidateQueries({ queryKey: ["projects"] }); });
    await settle(() => Boolean(container.textContent?.includes("접근 범위를 다시 확인하는 중")));
    expect(container.textContent).not.toContain("A stored synthetic answer");
    const editor = container.querySelector<HTMLTextAreaElement>("#live-problem")!;
    expect(editor.value).toBe("existing draft");
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(editor, "edited while checking");
      editor.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(readDraft("p", "t", scope("A"))).toBe("edited while checking");
    expect((container.querySelector('[aria-label="지시 추가"]') as HTMLButtonElement).disabled).toBe(true);
    release(); await settle(() => Boolean(container.textContent?.includes("A stored synthetic answer")));
    expect((container.querySelector("#live-problem") as HTMLTextAreaElement).value).toBe("edited while checking");
  } finally { release(); }
});

it("drops a missing thread selection but leaves that thread's draft untouched", async () => {
  fixture.threads = [];
  saveWorkspaceContext("p", "missing-thread", scope("A"));
  writeDraft("p", "missing-thread", "unsent old-thread draft", scope("A"));
  await mount(); await settle(() => Boolean(container.textContent?.includes("저장된 작업 선택 확인 필요")));
  expect(readWorkspaceContext(scope("A"))).toEqual({ projectId: "p", threadId: "" });
  expect(readDraft("p", "missing-thread", scope("A"))).toBe("unsent old-thread draft");
  expect(fixture.calls.some(call => call.method === "thread/read" && call.input.thread_id === "missing-thread")).toBe(false);
});

it("restores an unsent draft after a new browser mount in the same LOCAL workspace", async () => {
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "restart-safe draft", scope("A"));
  await mount(); await settle(() => Boolean(container.querySelector("#live-problem")));
  expect((container.querySelector("#live-problem") as HTMLTextAreaElement).value).toBe("restart-safe draft");
  await act(async () => root!.unmount()); root = undefined; client.clear(); container.remove();
  await mount(); await settle(() => Boolean(container.querySelector("#live-problem")));
  expect((container.querySelector("#live-problem") as HTMLTextAreaElement).value).toBe("restart-safe draft");
});

it("warns about a corrupt LOCAL draft without sending a new request or deleting its raw record", async () => {
  saveWorkspaceContext("p", "t", scope("A"));
  const key = `thoth:local-workspace:v1:draft:${JSON.stringify([ids.A, "p", "t"])}`;
  localStorage.setItem(key, "{corrupt-draft");
  await mount();
  await settle(() => Boolean(container.querySelector("#live-problem")));
  expect(container.textContent).toContain("이전 초안 저장 기록을 읽지 못했습니다");
  expect(localStorage.getItem(key)).toBe("{corrupt-draft");
  expect(fixture.calls.some(call => call.method === "thread/start" || call.method === "thread/input")).toBe(false);
  const editor = container.querySelector<HTMLTextAreaElement>("#live-problem")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(editor, "new explicit draft");
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(readDraft("p", "t", scope("A"))).toBe("new explicit draft");
  expect(container.textContent).not.toContain("이전 초안 저장 기록을 읽지 못했습니다");
  expect(fixture.calls.some(call => call.method === "thread/start" || call.method === "thread/input")).toBe(false);
});

it("keeps the editor usable and warns when durable draft storage fails", async () => {
  saveWorkspaceContext("p", "t", scope("A"));
  await mount(); await settle(() => Boolean(container.querySelector("#live-problem")));
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("synthetic quota"); });
  const editor = container.querySelector<HTMLTextAreaElement>("#live-problem")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(editor, "draft kept in the editor");
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(editor.value).toBe("draft kept in the editor");
  expect(container.textContent).toContain("이 브라우저에 초안을 저장하지 못했습니다");
});

it("keeps a draft after failed admission and clears it only after successful admission", async () => {
  fixture.executionReady = true; fixture.failAdmission = true;
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "send only after success", scope("A"));
  await mount(); await settle(() => Boolean(container.querySelector("#live-problem")));
  const form = container.querySelector("form.prompt-composer")!;
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await settle(() => Boolean(container.textContent?.includes("synthetic admission failure")));
  expect(readDraft("p", "t", scope("A"))).toBe("send only after success");
  fixture.failAdmission = false;
  await act(async () => form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await settle(() => readDraft("p", "t", scope("A")) === "");
  expect(fixture.calls.filter(call => call.method === "thread/input")).toHaveLength(2);
});

it("clears a new-thread draft only after the real-shape RUNNING start admission", async () => {
  fixture.executionReady = true;
  saveWorkspaceContext("p", "", scope("A")); writeDraft("p", "", "first synthetic question", scope("A"));
  await mount(); await settle(() => Boolean(container.querySelector<HTMLButtonElement>('[aria-label="첫 조사 시작"]:not([disabled])')));
  await act(async () => container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await settle(() => readDraft("p", "", scope("A")) === "");
  expect(fixture.calls.filter(call => call.method === "thread/start")).toHaveLength(1);
  expect(readWorkspaceContext(scope("A"))).toEqual({ projectId: "p", threadId: "t" });
});

it("reopens a lost accepted response without auto-submit and reuses the original key", async () => {
  fixture.executionReady = true; fixture.lostResponseOnce = true;
  saveWorkspaceContext("p", "", scope("A")); writeDraft("p", "", "original synthetic question", scope("A"));
  await mount(); await settle(() => Boolean(container.querySelector<HTMLButtonElement>('[aria-label="첫 조사 시작"]:not([disabled])')));
  await act(async () => container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await settle(() => Boolean(container.textContent?.includes("synthetic accepted response lost")));
  expect(readDraft("p", "", scope("A"))).toBe("original synthetic question");
  expect(readLocalPending(ids.A, "p", "").kind).toBe("PENDING");
  expect(fixture.createdOperations).toBe(1);
  await act(async () => root!.unmount()); root = undefined; client.clear(); container.remove();
  fixture.replayTerminal = true;
  await mount(); await settle(() => Boolean(container.textContent?.includes("이전 요청이 접수됐을 수 있습니다")));
  expect(fixture.calls.filter(call => call.method === "thread/start")).toHaveLength(1);
  const editor = container.querySelector<HTMLTextAreaElement>("#live-problem")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(editor, "a different unsent question");
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect((container.querySelector('[aria-label="첫 조사 시작"]') as HTMLButtonElement).disabled).toBe(true);
  const retry = [...container.querySelectorAll("button")].find(button => button.textContent === "이전 요청 같은 key로 확인")!;
  await act(async () => retry.click());
  await settle(() => readLocalPending(ids.A, "p", "").kind === "NONE");
  const calls = fixture.calls.filter(call => call.method === "thread/start");
  expect(calls).toHaveLength(2);
  expect(calls[0].key).toBe(calls[1].key);
  expect(calls[0].input).toEqual(calls[1].input);
  expect(fixture.createdOperations).toBe(1);
  expect(readDraft("p", "t", scope("A"))).toBe("a different unsent question");
});

it("does not transmit when the LOCAL pending key cannot be saved", async () => {
  fixture.executionReady = true;
  saveWorkspaceContext("p", "", scope("A")); writeDraft("p", "", "synthetic not yet sent", scope("A"));
  await mount(); await settle(() => Boolean(container.querySelector<HTMLButtonElement>('[aria-label="첫 조사 시작"]:not([disabled])')));
  const original = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
    if (key.includes(":pending:")) throw new Error("synthetic storage denial");
    original.call(this, key, value);
  });
  await act(async () => container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
  await settle(() => Boolean(container.textContent?.includes("중복 접수를 막기 위해 전송하지 않았습니다")));
  expect(fixture.calls.some(call => call.method === "thread/start")).toBe(false);
  expect(readDraft("p", "", scope("A"))).toBe("synthetic not yet sent");
});

it("retries the persisted model choice instead of replacing it with a new draft's default", async () => {
  fixture.executionReady = true;
  const selection = { provider: "test", model: "synthetic-model", reasoning_effort: "high" };
  const problem = "old synthetic instruction";
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", problem, scope("A"));
  expect(writeLocalPending({ version: 1, workspaceId: ids.A, projectId: "p", threadId: "t", problem, selection,
    signature: submissionSignature("p", "t", problem, selection), key: "22222222-2222-4222-8222-222222222222", createdAt: 1 })).toBe("SAVED");
  await mount(); await settle(() => Boolean(container.textContent?.includes("이전 요청이 접수됐을 수 있습니다")));
  expect((container.querySelector('[aria-label="지시 추가"]') as HTMLButtonElement).disabled).toBe(true);
  const retry = [...container.querySelectorAll("button")].find(button => button.textContent === "이전 요청 같은 key로 확인")!;
  await act(async () => retry.click());
  await settle(() => fixture.calls.some(call => call.method === "thread/input"));
  const call = fixture.calls.find(item => item.method === "thread/input")!;
  expect(call.key).toBe("22222222-2222-4222-8222-222222222222");
  expect(call.input).toMatchObject({ instruction: problem, provider: "test", model: "synthetic-model", reasoning_effort: "high" });
});

it("blocks a new operation when the restored pending marker is corrupt", async () => {
  fixture.executionReady = true;
  saveWorkspaceContext("p", "", scope("A")); writeDraft("p", "", "synthetic question", scope("A"));
  localStorage.setItem(`thoth:local-workspace:v1:pending:${JSON.stringify([ids.A, "p", ""])}`, "{corrupt");
  await mount(); await settle(() => Boolean(container.textContent?.includes("복구 정보를 확인하지 못했습니다")));
  expect((container.querySelector('[aria-label="첫 조사 시작"]') as HTMLButtonElement).disabled).toBe(true);
  expect(fixture.calls.some(call => call.method === "thread/start")).toBe(false);
});

it.each([{ kind: "QUEUED", admitted: true }, { kind: "COMPLETED_REPLAY", admitted: true }, { kind: "MALFORMED", admitted: false }] as const)(
  "keeps durable $kind admission distinct from an unaccepted response", async ({ kind, admitted }) => {
    fixture.executionReady = true; fixture.admissionKind = kind;
    saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "synthetic queued draft", scope("A"));
    await mount(); await settle(() => Boolean(container.querySelector("#live-problem")));
    await act(async () => container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    if (admitted) await settle(() => readDraft("p", "t", scope("A")) === "");
    else await settle(() => Boolean(container.textContent?.includes("접수를 확인하지 못했습니다")));
    expect(readDraft("p", "t", scope("A"))).toBe(admitted ? "" : "synthetic queued draft");
    expect(fixture.calls.filter(call => call.method === "thread/input")).toHaveLength(1);
  });

const QUEUED_NOTICE = "현재 조사가 끝나면 이 지시를 이어서 반영합니다";
async function submitDraft() {
  await act(async () => container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

it("shows a queued-instruction notice in the timeline until the running investigation ends", async () => {
  fixture.executionReady = true; fixture.admissionKind = "QUEUED"; fixture.threadOperation = "RUNNING";
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "synthetic queued draft", scope("A"));
  await mount(); await settle(() => Boolean(container.querySelector("#live-problem")));
  expect(container.textContent).not.toContain(QUEUED_NOTICE);
  await submitDraft();
  await settle(() => Boolean(container.textContent?.includes(QUEUED_NOTICE)));
  expect(container.querySelector(".conversation-timeline")?.textContent).toContain(QUEUED_NOTICE);
  fixture.threadOperation = "SUCCEEDED";
  await act(async () => { await client.invalidateQueries({ queryKey: ["research", "p", "t"] }); });
  await settle(() => !container.textContent?.includes(QUEUED_NOTICE));
});

it("opens the project at its most recently updated work and shows the project cutoff in the header", async () => {
  fixture.executionReady = true; fixture.threads = ["old-work", "t"];
  await mount(); await settle(() => Boolean(container.querySelector(".project-button")));
  expect(container.querySelector(".workstation-topbar")?.textContent).not.toContain("기준시점");
  await act(async () => (container.querySelector(".project-button") as HTMLButtonElement).click());
  await settle(() => fixture.calls.some(call => call.method === "thread/read"));
  const reads = fixture.calls.filter(call => call.method === "thread/read");
  expect(reads.every(call => call.input.thread_id === "t")).toBe(true);
  await settle(() => Boolean(container.textContent?.includes("stored synthetic answer")));
  expect(container.querySelector(".workstation-topbar")?.textContent).toContain("기준시점");
  expect(container.querySelector(".workstation-topbar")?.textContent).toContain("2026");
  expect(container.textContent).not.toContain("저장된 작업의 접근 범위를 다시 확인하는 중");
});

it("keeps the new-session screen when the user chooses 새 세션 after opening a project", async () => {
  fixture.executionReady = true;
  await mount(); await settle(() => Boolean(container.querySelector(".project-button")));
  await act(async () => (container.querySelector(".project-button") as HTMLButtonElement).click());
  await settle(() => Boolean(container.textContent?.includes("stored synthetic answer")));
  const fresh = container.querySelector(".new-session-button") as HTMLButtonElement;
  await act(async () => fresh.click());
  await settle(() => Boolean(container.querySelector('[aria-label="첫 조사 시작"]')));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 60)); });
  expect(container.querySelector('[aria-label="첫 조사 시작"]')).not.toBeNull();
  expect(container.textContent).not.toContain("stored synthetic answer");
});

it("keeps the typed first question and lets it start a new work when the work list arrives late", async () => {
  fixture.executionReady = true; fixture.threads = ["old-work"];
  let release!: () => void;
  fixture.threadListGate = new Promise<void>(resolve => { release = resolve; });
  await mount(); await settle(() => Boolean(container.querySelector(".project-button")));
  await act(async () => (container.querySelector(".project-button") as HTMLButtonElement).click());
  await settle(() => Boolean(container.querySelector("#live-problem")));
  await act(async () => {
    const input = container.querySelector<HTMLTextAreaElement>("#live-problem")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "synthetic first question");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => { release(); await new Promise(resolve => setTimeout(resolve, 60)); });
  await settle(() => Boolean(container.querySelector('[aria-label="첫 조사 시작"]')));
  expect(container.querySelector<HTMLTextAreaElement>("#live-problem")!.value).toBe("synthetic first question");
  expect(fixture.calls.some(call => call.method === "thread/read" && call.input.thread_id === "old-work")).toBe(false);
  await submitDraft();
  await settle(() => fixture.calls.some(call => call.method === "thread/start"));
  await settle(() => readWorkspaceContext(scope("A")).threadId === "t");
  expect(fixture.calls.some(call => call.method === "thread/read" && call.input.thread_id === "old-work")).toBe(false);
});

it("does not show the queued notice for a normally accepted instruction", async () => {
  fixture.executionReady = true; fixture.admissionKind = "RUNNING";
  saveWorkspaceContext("p", "t", scope("A")); writeDraft("p", "t", "synthetic normal draft", scope("A"));
  await mount(); await settle(() => Boolean(container.querySelector("#live-problem")));
  await submitDraft();
  await settle(() => readDraft("p", "t", scope("A")) === "");
  expect(container.textContent).not.toContain(QUEUED_NOTICE);
});
