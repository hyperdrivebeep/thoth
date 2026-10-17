// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readDraft, writeDraft } from "../api/conversation";
import { readDraftLanguage, writeDraftLanguage } from "../api/draftLanguage";
import { saveWorkspaceContext } from "../api/research";
import { setLocale, t, type Locale } from "../i18n/locale";
import { FileConnectionPanel } from "./FileConnectionPanel";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";

type Workspace = "A" | "B";
type Call = { method: string; input: Record<string, unknown>; workspace: Workspace; key: string };
const fixture = vi.hoisted(() => ({
  workspace: "A" as Workspace,
  calls: [] as Call[],
  sourceGate: null as Promise<void> | null,
  stageGate: null as Promise<void> | null,
  connectGate: null as Promise<void> | null,
  sourceHits: 0, stageHits: 0, connectHits: 0,
}));
const ids = { A: "workspace:m7-synthetic-a", B: "workspace:m7-synthetic-b" };
const scope = (workspace: Workspace) => ({ mode: "LOCAL" as const, workspaceId: ids[workspace] });

vi.mock("../api/rpcClient", async importOriginal => ({
  ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: async (method: string, input: Record<string, unknown>, key: string) => {
    const workspace = fixture.workspace;
    fixture.calls.push({ method, input, workspace, key });
    let value: unknown;
    if (method === "workspace/ready") value = {
      ready: true, execution_ready: true, workspace_readable: true, setup_complete: true,
      workspace_id: `workspace:m7-synthetic-${workspace.toLowerCase()}`, deployment_mode: "LOCAL",
      model_connected: true, setup: { internet_consent: "DENIED" },
      response_language_scope: "REQUEST", response_languages: ["ko", "en"],
    };
    else if (method === "project/list") value = { projects: ["p", "q"].map(project_id => ({
      project_id, name: `${workspace} project ${project_id}`, lifecycle: "ACTIVE", revision: 1,
      overlay: "synthetic", cutoff_at: "2026-10-01T00:00:00Z",
    })) };
    else if (method === "thread/list") value = { threads: [{
      project_id: input.project_id, thread_id: "t", problem: `${workspace} stored work`,
      updated_at: "2026-10-01T00:00:00Z", execution_state: "IDLE", lifecycle: "OPEN",
    }] };
    else if (method === "thread/read") value = {
      project_id: input.project_id, thread_id: input.thread_id, problem: "Synthetic stored work",
      lifecycle: "OPEN", execution_state: "IDLE", operation_state: "SUCCEEDED", current_result: null,
    };
    else if (method === "thread/activity/list") value = { conversation: { turns: [], next_before_epoch: null, history_limited: false } };
    else if (method === "project/source/list") {
      const gate = fixture.sourceGate;
      if (gate) { fixture.sourceGate = null; fixture.sourceHits += 1; await gate; }
      value = {
        artifacts: [{ artifact_id: "artifact:m7", source_uri: `${workspace}:${input.project_id}:source`, byte_sha256: "a".repeat(64), cutoff_state: "ELIGIBLE" }],
        source_times: [{ artifact_id: "artifact:m7", source_version_id: "source:m7", byte_sha256: "a".repeat(64), cutoff_state: "ELIGIBLE", revision: 1, assessment_digest: "b".repeat(64), cutoff_at: "2026-10-01T00:00:00Z" }],
        cutoff_basis: { cutoff_at: "2026-10-01T00:00:00Z", project_revision: 1 },
      };
    }
    else if (method === "evidence/list") value = { evidence: [] };
    else if (method === "model/settings/read") value = {
      settings_digest: null, selection: { provider: null, model: null, reasoning_effort: null },
      effective_settings: null, availability: "UNAVAILABLE", reason_code: "MODEL_CAPABILITY_UNKNOWN", model_options: [],
    };
    else if (method === "model/credential/list") value = { accounts: [] };
    else if (method === "thread/input" || method === "thread/start") value = {
      contract_version: 2, project_id: input.project_id, thread_id: "t", request_epoch: 2,
      operation_id: "operation:m7", status: "ACCEPTED_RUNNING",
    };
    else if (method === "project/source/connect") {
      fixture.connectHits += 1;
      const gate = fixture.connectGate;
      fixture.connectGate = null;
      if (gate) await gate;
      value = {};
    }
    else throw new Error(`UNEXPECTED_FAKE_RPC:${method}`);
    return { value, state: method.startsWith("thread/") && ["thread/input", "thread/start"].includes(method) ? "RUNNING" : "SUCCEEDED", operation_id: "operation:m7" };
  },
}));
vi.mock("../api/files", () => ({ stageFile: async (file: File) => {
  fixture.stageHits += 1;
  const gate = fixture.stageGate;
  fixture.stageGate = null;
  if (gate) await gate;
  return { relative_path: `web/${file.name}`, filename: file.name, media_type: "text/markdown", byte_sha256: "a".repeat(64), bytes: file.size };
} }));

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;
const releases: (() => void)[] = [];
const tick = () => new Promise(resolve => setTimeout(resolve, 15));
function deferred() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  releases.push(release);
  return { promise, release };
}
async function settle(check: () => boolean) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await act(async () => { await tick(); });
    if (check()) return;
  }
  throw new Error("NOT_TRIGGERED: synthetic UI did not reach the required boundary");
}
function editor() { return container.querySelector<HTMLTextAreaElement>("#live-problem"); }
function submissions() { return fixture.calls.filter(call => ["thread/start", "thread/input"].includes(call.method)); }
function sources(project: string) {
  return client.getQueryData<{ value: { artifacts: { source_uri: string }[] } }>(["sources", project])?.value.artifacts.map(item => item.source_uri) ?? [];
}
async function mount(node: ReactNode) {
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  await act(async () => { root!.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>); });
}
async function submit() {
  const button = container.querySelector<HTMLButtonElement>("form.prompt-composer button[type=submit]");
  expect(button?.disabled).toBe(false);
  await act(async () => { container.querySelector("form.prompt-composer")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
}
function observe(value: Record<string, unknown>) {
  // Only synthetic identifiers and explicit outcome fields; no provider payloads or secrets.
  console.info("M7_OBSERVATION " + JSON.stringify(value));
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  fixture.workspace = "A"; fixture.calls = [];
  fixture.sourceGate = null; fixture.stageGate = null; fixture.connectGate = null;
  fixture.sourceHits = 0; fixture.stageHits = 0; fixture.connectHits = 0;
  releases.length = 0; localStorage.clear(); sessionStorage.clear(); setLocale("ko");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) !== "/healthz") throw new Error("REAL_FETCH_FORBIDDEN");
    return new Response(JSON.stringify({ status: "ok", version: "synthetic" }));
  }));
  vi.stubGlobal("open", vi.fn(() => { throw new Error("REAL_BROWSER_FORBIDDEN"); }));
});
afterEach(async () => {
  if (root) await act(async () => { root!.unmount(); });
  root = undefined;
  await act(async () => { for (const release of releases) release(); await tick(); });
  client?.clear(); container?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  localStorage.clear(); sessionStorage.clear(); setLocale("ko");
});

it.each(["same-scope", "project-switch", "workspace-switch"] as const)(
  "W1 citation completion respects %s ownership", async mode => {
    const question = "Investigate synthetic artifact:m7";
    saveWorkspaceContext("p", "t", scope("A")); saveWorkspaceContext("p", "t", scope("B"));
    writeDraft("p", "t", question, scope("A"));
    writeDraft("q", "t", "new Q draft", scope("A")); writeDraft("p", "t", "new B draft", scope("B"));
    await mount(<LiveProjectWorkspace />);
    await settle(() => editor()?.value === question && client.isFetching() === 0);
    const gate = deferred(); fixture.sourceGate = gate.promise;
    await submit();
    await settle(() => fixture.sourceHits === 1);
    expect(submissions()).toHaveLength(0);
    const staleCacheWrites: string[] = [];
    const unsubscribe = client.getQueryCache().subscribe(event => {
      if (mode === "workspace-switch" && fixture.workspace === "B" && event.type === "updated" && event.action.type === "success" && event.query.queryKey[0] === "sources" && event.query.queryKey[1] === "p") {
        const data = event.query.state.data as { value?: { artifacts?: { source_uri: string }[] } } | undefined;
        for (const item of data?.value?.artifacts ?? []) if (item.source_uri === "A:p:source") staleCacheWrites.push(item.source_uri);
      }
    });
    try {
      if (mode === "project-switch") {
        const button = [...container.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent?.includes("A project q"));
        expect(button).toBeTruthy();
        await act(async () => { button!.click(); });
        await settle(() => editor()?.value === "new Q draft" && client.isFetching() === 0);
      } else if (mode === "workspace-switch") {
        fixture.workspace = "B";
        await act(async () => { await client.invalidateQueries({ queryKey: ["workspace-ready"] }); });
        await settle(() => editor()?.value === "new B draft" && client.isFetching() === 0);
        expect(sources("p")).toEqual(["B:p:source"]);
      }
      await act(async () => { gate.release(); await tick(); });
      await settle(() => client.isMutating() === 0 && client.isFetching() === 0);
      // Include transient cache contamination even if a later refetch repairs the final value.
      observe({ case: `W1:${mode}`, sourceHits: fixture.sourceHits, sends: submissions().map(call => ({ project: call.input.project_id, workspace: call.workspace })), staleCacheWrites, currentDraft: editor()?.value, oldDraftPreserved: readDraft("p", "t", scope("A")) === question });
      if (mode === "same-scope") {
        expect(submissions()).toHaveLength(1);
        expect(submissions()[0].input.project_id).toBe("p");
        expect(editor()?.value).toBe("");
      } else {
        expect.soft(submissions()).toHaveLength(0);
        expect.soft(staleCacheWrites).toEqual([]);
        expect.soft(editor()?.value).toBe(mode === "project-switch" ? "new Q draft" : "new B draft");
        expect.soft(readDraft("p", "t", scope("A"))).toBe(question);
      }
    } finally { unsubscribe(); }
  },
);

it.each(["stage", "connect"] as const)("W2 a newer file selection survives prior %s completion", async phase => {
  await mount(<FileConnectionPanel projectId="p" />);
  const input = container.querySelector<HTMLInputElement>('input[type="file"]')!;
  let nativeValue = "";
  Object.defineProperty(input, "value", { configurable: true, get: () => nativeValue, set: (value: string) => { nativeValue = value; } });
  async function selectFile(name: string) {
    nativeValue = `C:\\fakepath\\${name}`;
    Object.defineProperty(input, "files", { configurable: true, value: [new File(["synthetic"], name, { type: "text/markdown" })] });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
  }
  await selectFile("A.md");
  await act(async () => {
    const visibility = container.querySelector<HTMLSelectElement>(`[aria-label="${t("sources.scopeLabel")}"]`)!;
    visibility.value = "PROJECT_SHARED"; visibility.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const gate = deferred();
  if (phase === "stage") fixture.stageGate = gate.promise; else fixture.connectGate = gate.promise;
  const button = [...container.querySelectorAll<HTMLButtonElement>("button")].find(item => item.textContent === t("composer.attach"))!;
  expect(button.disabled).toBe(false);
  await act(async () => { button.click(); });
  await settle(() => phase === "stage" ? fixture.stageHits === 1 : fixture.connectHits === 1);
  expect(input.disabled).toBe(false);
  await selectFile("B.md");
  expect(container.textContent).toContain("B.md");
  await act(async () => { gate.release(); await tick(); });
  await settle(() => client.isMutating() === 0);
  observe({ case: `W2:${phase}`, stageHits: fixture.stageHits, connectHits: fixture.connectHits, nativeValue, selectedBVisible: container.textContent?.includes("B.md") });
  expect.soft(nativeValue).toBe("C:\\fakepath\\B.md");
  expect.soft(container.textContent).toContain("B.md");
  expect(fixture.calls.filter(call => call.method === "project/source/connect")).toHaveLength(1);
});

it.each([["ko", "en"], ["en", "ko"]] as const)("W3 existing storage errors follow %s to %s locale changes", async (from: Locale, to: Locale) => {
  setLocale(from);
  saveWorkspaceContext("p", "t", scope("A"));
  writeDraft("p", "t", "Synthetic unchanged draft", scope("A"));
  writeDraftLanguage(scope("A"), "p", "t", "en");
  await mount(<LiveProjectWorkspace />);
  await settle(() => editor()?.value === "Synthetic unchanged draft" && client.isFetching() === 0);
  const original = Storage.prototype.setItem;
  let faults = 0;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
    if (key.includes(":pending:")) { faults += 1; throw new Error("SYNTHETIC_PENDING_WRITE_DENIED"); }
    original.call(this, key, value);
  });
  await submit();
  await settle(() => faults === 1 && Boolean(container.textContent?.includes(t("submission.storageFailed", from))));
  expect(submissions()).toHaveLength(0);
  const callCount = fixture.calls.length;
  const stored = Object.fromEntries(Object.keys(localStorage).filter(key => key !== "thoth:display-locale:v1").map(key => [key, localStorage.getItem(key)]));
  await act(async () => { setLocale(to); await tick(); });
  const after = Object.fromEntries(Object.keys(localStorage).filter(key => key !== "thoth:display-locale:v1").map(key => [key, localStorage.getItem(key)]));
  observe({ case: `W3:${from}-${to}`, faults, oldErrorPresent: container.textContent?.includes(t("submission.storageFailed", from)), newErrorPresent: container.textContent?.includes(t("submission.storageFailed", to)), extraRpc: fixture.calls.length - callCount, storedUnchanged: JSON.stringify(stored) === JSON.stringify(after) });
  expect.soft(container.textContent).toContain(t("submission.storageFailed", to));
  expect.soft(container.textContent).not.toContain(t("submission.storageFailed", from));
  expect(fixture.calls).toHaveLength(callCount);
  expect(after).toEqual(stored);
  expect(editor()?.value).toBe("Synthetic unchanged draft");
  expect(readDraftLanguage(scope("A"), "p", "t")).toBe("en");
});
