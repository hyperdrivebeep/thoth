// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useRef, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { readDraft, writeDraft } from "../api/conversation";
import { readResponseLanguageDefault, writeResponseLanguageDefault } from "../api/responseLanguageDefault";
import { readLocalPending, submissionSignature, writeLocalPending } from "../api/localWorkspacePersistence";
import { saveWorkspaceContext } from "../api/research";
import { readResearchStatus } from "../api/researchStatusRead";
import progressFixture from "../api/researchStatusRead.fixture.json";
import { setLocale, t } from "../i18n/locale";
import { ArchivedProjects } from "./ArchivedProjects";
import { LiveProjectWorkspace } from "./LiveProjectWorkspace";
import { useResearchSession } from "./useResearchSession";

const fixture = vi.hoisted(() => ({ target: "research", phase: "ok", running: false, byteCase: false,
  calls: [] as { method: string; input: Record<string, unknown>; phase: string }[] }));
const WORKSPACE = "workspace:live-read-protection";
const scope = { mode: "LOCAL" as const, workspaceId: WORKSPACE };
const DRAFT = "Synthetic unsent draft";
const PENDING_KEY = "11111111-1111-4111-8111-111111111111";
const markers = { sources: ["SOURCE_OLD", "SOURCE_NEW"], research: ["RESEARCH_OLD", "RESEARCH_NEW"], archive: ["ARCHIVE_OLD", "ARCHIVE_NEW"] };
vi.mock("../api/rpcClient", async importOriginal => {
  const actual = await importOriginal<typeof import("../api/rpcClient")>();
  return { ...actual, rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push({ method, input, phase: fixture.phase });
    if (fixture.byteCase) return structuredClone(progressFixture.light);
    const targeted = fixture.target === "sources" && method === "project/source/list"
      || fixture.target === "research" && method === "thread/read"
      || fixture.target === "archive" && method === "project/list" && input.include_archived === true;
    if (targeted && fixture.phase === "denied") throw new actual.RpcError("Synthetic read denied", -32040, { reason_code: "RESOURCE_ACCESS_DENIED" });
    if (targeted && fixture.phase === "network") throw new actual.RpcTransportError("Synthetic network failure", "NETWORK");
    const fresh = fixture.phase === "recovered";
    let value: unknown;
    if (method === "workspace/ready") value = { ready: true, execution_ready: true, workspace_readable: true, setup_complete: true, workspace_id: WORKSPACE,
      deployment_mode: "LOCAL", model_connected: true, setup: { internet_consent: "DENIED" }, response_language_scope: "REQUEST", response_languages: ["ko", "en"] };
    else if (method === "project/list") value = { projects: [{ project_id: input.include_archived ? "archived:p" : "p", name: input.include_archived ? markers.archive[fresh ? 1 : 0] : "Synthetic project", lifecycle: input.include_archived ? "ARCHIVED_READ_ONLY" : "ACTIVE", revision: 1, overlay: "synthetic", cutoff_at: "2026-10-01T00:00:00Z" }] };
    else if (method === "thread/list") value = { threads: [{ project_id: "p", thread_id: "t", problem: "Synthetic thread", execution_state: "IDLE", lifecycle: "OPEN" }] };
    else if (method === "thread/read") {
      if (input.view === "PROGRESS") value = { project_id: "p", thread_id: "t", operation_state: "RUNNING", usage: { total_tokens: 9 } };
      else value = { project_id: "p", thread_id: "t", problem: "Synthetic thread", lifecycle: "OPEN", execution_state: fixture.running ? "RUNNING" : "IDLE", current_object_ids: [],
        operation_state: fixture.running ? "RUNNING" : "SUCCEEDED", request: { operation_id: "operation:current", authored_text: "Synthetic accepted question", response_language: fresh ? "ko" : "en" },
        current_result: { request_ref: { revision_digest: "a".repeat(64) }, operation_id: "operation:current", completion: "TERMINAL", terminal_reason: "BOUNDED_RESEARCH_COMPLETE", result: { answer: markers.research[fresh ? 1 : 0], reference: { note: markers.research[fresh ? 1 : 0] } } },
        ...(fresh ? {} : { coverage_matrix: { old_only: "RESEARCH_OLD" } }) };
    }
    else if (method === "thread/activity/list") value = { conversation: { turns: [], next_before_epoch: null, history_limited: false } };
    else if (method === "project/source/list") value = { artifacts: [{ artifact_id: "artifact:x", source_uri: markers.sources[fresh ? 1 : 0], media_type: "text/markdown", cutoff_state: "ELIGIBLE" }] };
    else if (method === "evidence/list") value = { evidence: [] };
    else if (method === "model/settings/read") value = { settings_digest: null, selection: { provider: null, model: null, reasoning_effort: null }, effective_settings: null, availability: "UNAVAILABLE", model_options: [] };
    else if (method === "model/credential/list") value = { accounts: [] };
    else if (method === "hypothesis/link/list") value = { links: [] };
    else throw new Error(`UNEXPECTED_FAKE_RPC:${method}`);
    return { operation_id: "operation:read", state: "SUCCEEDED", value };
  } };
});

let root: Root | undefined, container: HTMLDivElement, client: QueryClient;
let session: ReturnType<typeof useResearchSession>;
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
async function settle(check: () => boolean) { for (let i = 0; i < 70; i++) { await act(async () => { await tick(); }); if (check()) return; } throw new Error("NOT_TRIGGERED"); }
const button = (text: string) => [...document.body.querySelectorAll<HTMLButtonElement>("button")].find(node => node.textContent?.trim() === text);
async function click(node: HTMLElement | undefined) { expect(node).toBeTruthy(); await act(async () => { node!.click(); await tick(); }); }
function stored() { return Object.fromEntries(Object.keys(localStorage).map(key => [key, localStorage.getItem(key)])); }
async function mount(node: ReactElement) {
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(["workspace-ready"], { value: { response_language_scope: "REQUEST", response_languages: ["ko", "en"] } });
  // LiveProjectWorkspace must read its full readiness value itself.
  if (node.type === LiveProjectWorkspace) client.removeQueries({ queryKey: ["workspace-ready"] });
  await act(async () => { root!.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>); });
}
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); fixture.target = "research"; fixture.phase = "ok"; fixture.running = false; fixture.byteCase = false; fixture.calls = [];
  localStorage.clear(); sessionStorage.clear(); setLocale("ko"); saveWorkspaceContext("p", "t", scope); writeDraft("p", "t", DRAFT, scope); writeResponseLanguageDefault(scope, "en");
  expect(writeLocalPending({ version: 2, workspaceId: WORKSPACE, projectId: "p", threadId: "t", problem: DRAFT, selection: null, responseLanguage: "en", signature: submissionSignature("p", "t", DRAFT, null, null, "en"), key: PENDING_KEY, createdAt: 1 })).toBe("SAVED");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) !== "/healthz") throw new Error("REAL_FETCH_FORBIDDEN");
    return new Response(JSON.stringify({ status: "ok", version: "synthetic" }));
  }));
});
afterEach(async () => { if (root) await act(async () => { root!.unmount(); await tick(); }); root = undefined; client?.clear(); container?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); });

it.each(["sources", "research", "archive"] as const)("protects %s through denial, transient failure and a fresh read", async target => {
  fixture.target = target;
  await mount(target === "archive" ? <ArchivedProjects scopeKey={`LOCAL:${WORKSPACE}`} /> : <LiveProjectWorkspace />);
  if (target === "archive") await click(button(t("archive.show")));
  else await settle(() => Boolean(container.querySelector("#live-problem")) && client.isFetching() === 0);
  if (target === "sources") await click([...container.querySelectorAll<HTMLElement>('[role="tab"]')].find(node => node.textContent?.trim() === t("workspace.resources")));
  await settle(() => Boolean(document.body.textContent?.includes(markers[target][0])) && client.isFetching() === 0);
  if (target === "research") {
    await click(button(t("result.more"))); await click(button(t("result.references")));
    expect(container.querySelector(".research-detail-pane, .detail-pane, .detail-scroll")?.textContent).toContain(markers.research[0]);
  }
  const saved = stored();
  const key = target === "sources" ? ["sources", "p"] : target === "research" ? ["research", "p", "t"] : ["projects", `LOCAL:${WORKSPACE}`, "archived"];
  fixture.phase = "network";
  await act(async () => { await client.refetchQueries({ queryKey: key, exact: true }); });
  expect(document.body.textContent).toContain(markers[target][0]);
  fixture.phase = "denied";
  await act(async () => { await client.refetchQueries({ queryKey: key, exact: true }); });
  await settle(() => !document.body.textContent?.includes(markers[target][0]));
  if (target === "archive") expect(container.querySelector(".archived-project-row button")).toBeNull();
  fixture.phase = "network";
  await act(async () => { await client.refetchQueries({ queryKey: key, exact: true }); });
  expect(document.body.textContent).not.toContain(markers[target][0]);
  fixture.phase = "recovered";
  await act(async () => { await client.refetchQueries({ queryKey: key, exact: true }); });
  await settle(() => Boolean(document.body.textContent?.includes(markers[target][1])));
  expect(document.body.textContent).not.toContain(markers[target][0]);
  expect(stored()).toEqual(saved);
  expect(readDraft("p", "t", scope)).toBe(DRAFT);
  expect(readResponseLanguageDefault(scope)).toBe("en");
  const pending = readLocalPending(WORKSPACE, "p", "t"); expect(pending.kind).toBe("PENDING"); if (pending.kind === "PENDING") expect(pending.value.key).toBe(PENDING_KEY);
});

function ResearchProbe() { const epoch = useRef(0); session = useResearchSession("p", "t", epoch, () => {}, scope, true); return <output>{JSON.stringify(session.research.data?.value ?? {})}</output>; }
it("uses full reads after PROGRESS denial and network failure, then resumes progress merging", async () => {
  fixture.running = true;
  await mount(<ResearchProbe />); await settle(() => Boolean(container.textContent?.includes("RESEARCH_OLD")));
  const key = ["research", "p", "t"];
  await act(async () => { await client.refetchQueries({ queryKey: key, exact: true }); });
  expect(fixture.calls.at(-1)?.input.view).toBe("PROGRESS");
  expect(container.textContent).toContain("RESEARCH_OLD");
  fixture.phase = "denied";
  await act(async () => { await client.refetchQueries({ queryKey: key, exact: true }); });
  await settle(() => session.researchReadDenied && !container.textContent?.includes("RESEARCH_OLD"));
  expect(fixture.calls.at(-1)?.input.view).toBe("PROGRESS");
  expect(session.researchReadDenied).toBe(true); expect(container.textContent).not.toContain("RESEARCH_OLD");
  fixture.phase = "network";
  await act(async () => { await client.refetchQueries({ queryKey: key, exact: true }); });
  expect(fixture.calls.at(-1)?.input.view).toBeUndefined(); expect(container.textContent).not.toContain("RESEARCH_OLD");
  fixture.phase = "recovered";
  await act(async () => { await client.refetchQueries({ queryKey: key, exact: true }); });
  await settle(() => !session.researchReadDenied && Boolean(container.textContent?.includes("RESEARCH_NEW")));
  expect(fixture.calls.at(-1)?.input.view).toBeUndefined(); expect(session.researchReadDenied).toBe(false);
  expect(container.textContent).toContain("RESEARCH_NEW"); expect(container.textContent).not.toContain("old_only");
  await act(async () => { await client.refetchQueries({ queryKey: key, exact: true }); });
  expect(fixture.calls.at(-1)?.input.view).toBe("PROGRESS"); expect(container.textContent).toContain("RESEARCH_NEW");
  expect(session.problem).toBe(DRAFT); expect(session.responseLanguage).toBe("en"); expect(session.pendingRead.kind).toBe("PENDING");
});

it("preserves ordinary PROGRESS merged JSON bytes captured before this change", async () => {
  fixture.byteCase = true;
  client = new QueryClient(); const key = ["research", "p", "t"];
  client.setQueryData(key, structuredClone(progressFixture.prior)); client.setQueryData(["workspace-ready"], structuredClone(progressFixture.ready));
  const result = await readResearchStatus(client, key, "p", "t");
  expect(JSON.stringify(result)).toBe(progressFixture.expectedJson);
  expect(fixture.calls.map(({ method, input }) => ({ method, input }))).toEqual(progressFixture.expectedCalls);
});
