// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import type { ImportMode, ImportPreview } from "../api/trace";
import { setLocale, t, useLocale } from "../i18n/locale";
import { HypothesisCompareLive } from "./HypothesisCompareLive";
import type { MemoryRevision } from "./memoryEdit";
import { ProjectMemoryPanel } from "./ProjectMemoryPanel";
import { TraceEvidenceDialog } from "./TraceEvidenceDialog";
import { TraceImportDialog } from "./TraceImportDialog";
import { ProjectReviewList } from "./history/ProjectReviewList";

const fixture = vi.hoisted(() => ({
  readMode: "ok" as "ok" | "denied" | "network",
  previewGates: new Map<string, Promise<void>>(),
  saveGate: null as Promise<void> | null,
  calls: [] as { method: string; input: Record<string, unknown> }[],
}));
const EVIDENCE = "SYNTHETIC_OLD_EVIDENCE";
const REVIEW = "SYNTHETIC_OLD_REVIEW_ITEM";
function memory(id: "A" | "B"): MemoryRevision {
  return {
    memory_id: `memory:${id}`, memory_revision_id: `memory-revision:${id}`,
    revision_digest: id.toLowerCase().repeat(64), parent_revision_digest: null,
    owner_revision_ref: `owner:${id}`, kind: "LESSON", summary: `Synthetic memory ${id}`,
    assertion: `Synthetic memory ${id}`, source_ref: null, evidence_count: 0, evidence_refs: [],
    transition: "COMMIT", support_status: "SUPPORTED", authority_status: "AUTHORITATIVE",
    cutoff_valid: true, is_latest: true, not_recalled_because: null, created_at: "2026-10-01T00:00:00Z",
  };
}
function preview(csv: string, mode: ImportMode): ImportPreview {
  return {
    preview_id: `preview:${csv}:${mode}`, input_sha256: "a".repeat(64), mode,
    current_set_digest: null, applicable: true,
    changes: { added: [{ row: 1, kind: "ITEM", id: `PREVIEW_${csv}_${mode}`, fields: [] }], updated: [], deleted: [], unchanged: 0 },
    conflicts: [], losses: [], spreadsheet_suspects: [],
  };
}
vi.mock("../api/rpcClient", async importOriginal => {
  const original = await importOriginal<typeof import("../api/rpcClient")>();
  return { ...original, rpc: async (method: string, input: Record<string, unknown>) => {
    fixture.calls.push({ method, input });
    let value: unknown;
    if (["evidence/list", "project/review/list"].includes(method) && fixture.readMode !== "ok") {
      if (fixture.readMode === "denied") throw new original.RpcError("SYNTHETIC_DENIAL", -32040, { reason_code: "RESOURCE_ACCESS_DENIED" });
      throw new original.RpcTransportError("SYNTHETIC_NETWORK_FAILURE", "NETWORK");
    }
    if (method === "evidence/list") value = { evidence: [{
      span_id: "span:synthetic", artifact_id: "artifact:synthetic", source_version_id: "source:synthetic",
      exact_text: EVIDENCE, authority_state: "AUTHORITATIVE", cutoff_state: "ELIGIBLE",
      verification_state: "UNVERIFIED", support_state: "SUPPORTED", locator: { line: 1 },
    }] };
    else if (method === "project/review/list") value = {
      project_id: "project:p", coverage: "COMPLETE_PAGE", items: [{
        item_id: "item:synthetic", project_id: "project:p", thread_id: "thread:t",
        request_revision_digest: "a".repeat(64), result_revision_digest: null, title: REVIEW,
        priority: "HIGH", reason_codes: [], currentness: { state: "REVIEW_REQUIRED", reasons: [] },
        next_user_action: { action_type: "REVIEW_CURRENTNESS", label: "Synthetic review", reason_codes: [], requires_permission: false, target: null, basis: {} },
      }], next_cursor: null, unread_supported: false, assignment_supported: false,
    };
    else if (method === "trace/importPreview") {
      const csv = String(input.csv_text), mode = input.mode as ImportMode;
      const gate = fixture.previewGates.get(`${csv}:${mode}`);
      if (gate) { fixture.previewGates.delete(`${csv}:${mode}`); await gate; }
      value = preview(csv, mode);
    }
    else if (method === "memory/revision/list") value = { revisions: [memory("A"), memory("B")], counts: { total: 2, recallable: 2, not_recalled: 0 } };
    else if (method === "memory/settings/read") value = { memory_injection: true, query_expansion: true, settings_digest: null };
    else if (method === "memory/edit/propose") {
      if (fixture.saveGate) await fixture.saveGate;
      value = { transition: "HOLD", revision: { support_status: "CONFLICTING" } };
    }
    else if (method === "hypothesis/review/list") value = { requests: [] };
    else if (method === "hypothesis/read") value = { hypothesis: { hypothesis_id: input.hypothesis_id, revision_digest: "d".repeat(64) } };
    else if (method === "hypothesis/review/request") {
      if (fixture.saveGate) await fixture.saveGate;
      value = { request: { request_id: "request:synthetic" }, created: true, instruction: "ACCEPTED" };
    }
    else if (["hypothesis/link/list", "hypothesis/same/list", "hypothesis/test/result/list", "trace/lesson/list"].includes(method)) value = {};
    else throw new Error(`UNEXPECTED_FAKE_RPC:${method}`);
    return { state: "SUCCEEDED", operation_id: "operation:synthetic", value };
  } };
});

let root: Root | undefined, container: HTMLDivElement, client: QueryClient;
const releases: (() => void)[] = [];
const tick = (ms = 20) => new Promise(resolve => setTimeout(resolve, ms));
function deferred() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  releases.push(release);
  return { promise, release };
}
function dialog() { return document.body.querySelector<HTMLElement>(".bp6-dialog"); }
function button(label: string, scope: ParentNode = document.body) {
  return [...scope.querySelectorAll<HTMLButtonElement>("button")].find(node => node.textContent?.trim() === label);
}
async function settle(check: () => boolean) {
  for (let i = 0; i < 70; i += 1) {
    await act(async () => { await tick(); });
    if (check()) return;
  }
  throw new Error("NOT_TRIGGERED: synthetic boundary was not reached");
}
async function click(node: HTMLElement | undefined | null) {
  expect(node).toBeTruthy();
  await act(async () => { node!.click(); await tick(); });
}
async function write(field: HTMLTextAreaElement | null, text: string) {
  expect(field).toBeTruthy();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, text);
    field!.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function mount(node: ReactNode) {
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  await act(async () => { root!.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>); });
}
function observe(data: Record<string, unknown>) { console.info("M7_CLOSE_OBSERVATION " + JSON.stringify(data)); }
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  fixture.readMode = "ok"; fixture.calls = []; fixture.previewGates.clear(); fixture.saveGate = null;
  releases.length = 0; localStorage.clear(); sessionStorage.clear(); setLocale("ko");
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("REAL_NETWORK_FORBIDDEN"); }));
  vi.stubGlobal("open", vi.fn(() => { throw new Error("REAL_BROWSER_FORBIDDEN"); }));
});
afterEach(async () => {
  if (root) await act(async () => { root!.unmount(); });
  root = undefined;
  await act(async () => { releases.forEach(release => release()); await tick(); });
  client?.clear(); container?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  localStorage.clear(); sessionStorage.clear(); setLocale("ko");
});

it.each(["denied", "network"] as const)("CLOSE-05 handles %s refetch without confusing retained content with permission", async mode => {
  await mount(<><TraceEvidenceDialog projectId="project:p" spanRef="span:synthetic" onClose={() => {}} />
    <ProjectReviewList projectId="project:p" onSelect={() => {}} /></>);
  await settle(() => Boolean(document.body.textContent?.includes(EVIDENCE) && document.body.textContent.includes(REVIEW)));
  fixture.readMode = mode;
  await act(async () => { await client.refetchQueries({ type: "active" }); });
  await settle(() => client.isFetching() === 0 && client.getQueryState(["trace-evidence-spans", "project:p"])?.status === "error"
    && client.getQueryState(["project-review-list", "project:p"])?.status === "error");
  const evidenceVisible = document.body.textContent?.includes(EVIDENCE) ?? false;
  const reviewVisible = document.body.textContent?.includes(REVIEW) ?? false;
  observe({ case: `CLOSE-05:${mode}`, evidenceVisible, reviewVisible, readCounts: fixture.calls.map(call => call.method) });
  if (mode === "denied") {
    expect.soft(evidenceVisible).toBe(false);
    expect.soft(reviewVisible).toBe(false);
  } else {
    expect(evidenceVisible).toBe(true); expect(reviewVisible).toBe(true);
    expect(document.body.textContent).toContain(t("traceEvidence.failed"));
    expect(document.body.textContent).toContain(t("reviewList.pageFailed"));
  }
});

function ImportSurface() {
  const [open, setOpen] = useState(true);
  return <><button onClick={() => setOpen(true)}>Reopen synthetic import</button>
    <TraceImportDialog projectId="project:p" isOpen={open} hasTrace={false} onClose={() => setOpen(false)} onApplied={() => { throw new Error("APPLY_FORBIDDEN_IN_PREVIEW_TEST"); }} /></>;
}
async function chooseCsv(text: string) {
  const file = new File([text], `${text}.csv`, { type: "text/csv" });
  Object.defineProperty(file, "text", { value: async () => text });
  const field = dialog()!.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(field, "files", { configurable: true, value: [file] });
  await act(async () => { field.dispatchEvent(new Event("change", { bubbles: true })); await tick(); });
}
it("CLOSE-01 keeps the newest file and mode preview when A resolves after B", async () => {
  const gate = deferred(); fixture.previewGates.set("A:CREATE", gate.promise);
  await mount(<ImportSurface />); await settle(() => Boolean(dialog()));
  await chooseCsv("A");
  expect(fixture.calls.filter(call => call.method === "trace/importPreview")).toHaveLength(1);
  await click(dialog()!.querySelector<HTMLInputElement>('input[value="UPDATE"]'));
  await chooseCsv("B");
  await settle(() => Boolean(dialog()?.textContent?.includes("PREVIEW_B_UPDATE")));
  await act(async () => { gate.release(); await tick(); });
  observe({ case: "CLOSE-01:reversed", oldVisible: dialog()?.textContent?.includes("PREVIEW_A_CREATE"), newVisible: dialog()?.textContent?.includes("PREVIEW_B_UPDATE"), selectedB: dialog()?.textContent?.includes("B.csv") });
  expect.soft(dialog()?.textContent).toContain("PREVIEW_B_UPDATE");
  expect.soft(dialog()?.textContent).not.toContain("PREVIEW_A_CREATE");
  expect(dialog()!.querySelector<HTMLInputElement>('input[value="UPDATE"]')?.checked).toBe(true);
  expect(dialog()?.textContent).toContain("B.csv");
});
it("CLOSE-01 does not adopt the previous opening's preview after close and reopen", async () => {
  const gate = deferred(); fixture.previewGates.set("A:CREATE", gate.promise);
  await mount(<ImportSurface />); await settle(() => Boolean(dialog())); await chooseCsv("A");
  expect(fixture.calls.filter(call => call.method === "trace/importPreview")).toHaveLength(1);
  await click(button(t("hold.close"), dialog()!)); await settle(() => !dialog());
  await click(button("Reopen synthetic import", container)); await settle(() => Boolean(dialog()));
  expect(dialog()?.querySelector(".trace-preview")).toBeNull();
  await act(async () => { gate.release(); await tick(); });
  observe({ case: "CLOSE-01:reopened", oldVisible: dialog()?.textContent?.includes("PREVIEW_A_CREATE"), previewPresent: Boolean(dialog()?.querySelector(".trace-preview")) });
  expect.soft(dialog()?.querySelector(".trace-preview")).toBeNull();
  expect.soft(dialog()?.textContent).not.toContain("PREVIEW_A_CREATE");
});

function memoryCard(id: string) { return [...container.querySelectorAll<HTMLElement>(".memory-card")].find(node => node.textContent?.includes(`Synthetic memory ${id}`))!; }
it("CLOSE-02 memory A completion does not close or reset the newly opened B draft", async () => {
  const gate = deferred(); fixture.saveGate = gate.promise;
  await mount(<ProjectMemoryPanel projectId="project:p" />); await settle(() => Boolean(container.querySelector("[data-memory-ready]")));
  await click(memoryCard("A").querySelector("button"));
  await write(dialog()!.querySelectorAll("textarea")[0], "A correction");
  await write(dialog()!.querySelectorAll("textarea")[1], "A reason");
  await click(button(t("memoryEdit.send"), dialog()!));
  await settle(() => fixture.calls.some(call => call.method === "memory/edit/propose"));
  expect(fixture.calls.find(call => call.method === "memory/edit/propose")!.input.target_revision_digest).toBe("a".repeat(64));
  await click(button(t("hold.close"), dialog()!)); await settle(() => !dialog());
  await click(memoryCard("B").querySelector("button"));
  await write(dialog()!.querySelectorAll("textarea")[0], "B correction must remain");
  await write(dialog()!.querySelectorAll("textarea")[1], "B reason must remain");
  expect(dialog()?.textContent).toContain("Synthetic memory B");
  await act(async () => { gate.release(); await tick(); });
  await settle(() => client.isMutating() === 0 && client.isFetching() === 0);
  await act(async () => { await tick(350); });
  const values = [...(dialog()?.querySelectorAll("textarea") ?? [])].map(node => node.value);
  observe({ case: "CLOSE-02:memory", dialogOpen: Boolean(dialog()), values });
  expect.soft(dialog()).not.toBeNull();
  expect.soft(dialog()?.textContent ?? "").toContain("Synthetic memory B");
  expect.soft(values).toEqual(["B correction must remain", "B reason must remain"]);
});

it("CLOSE-02 review A completion does not close or reset the newly opened B draft", async () => {
  const gate = deferred(); fixture.saveGate = gate.promise;
  const result = structuredClone(excerpt) as Record<string, unknown>;
  const hypotheses = (excerpt as { portfolio: { hypotheses: { hypothesis_id: string; statement: string }[] } }).portfolio.hypotheses;
  await mount(<HypothesisCompareLive projectId="project:p" threadId="thread:t" result={result} />);
  const reviewButtons = () => [...container.querySelectorAll<HTMLButtonElement>("button")].filter(node => node.textContent?.trim() === "재검토 요청");
  await settle(() => reviewButtons().length >= 2);
  await click(reviewButtons()[0]); await click(dialog()!.querySelector<HTMLInputElement>('input[type="checkbox"]'));
  await write(dialog()!.querySelector("textarea"), "A review note");
  await click(button(t("reviewDialog.send"), dialog()!));
  await settle(() => fixture.calls.some(call => call.method === "hypothesis/review/request"));
  expect(fixture.calls.find(call => call.method === "hypothesis/review/request")!.input.hypothesis_id).toBe(hypotheses[0].hypothesis_id);
  await click(button(t("hold.close"), dialog()!)); await settle(() => !dialog());
  await click(reviewButtons()[1]); await click(dialog()!.querySelector<HTMLInputElement>('input[type="checkbox"]'));
  await write(dialog()!.querySelector("textarea"), "B review note must remain");
  expect(dialog()?.textContent).toContain(hypotheses[1].statement);
  await act(async () => { gate.release(); await tick(); });
  await settle(() => client.isMutating() === 0 && client.isFetching() === 0);
  await act(async () => { await tick(350); });
  observe({ case: "CLOSE-02:review", dialogOpen: Boolean(dialog()), note: dialog()?.querySelector("textarea")?.value });
  expect.soft(dialog()).not.toBeNull();
  expect.soft(dialog()?.textContent ?? "").toContain(hypotheses[1].statement);
  expect.soft(dialog()?.querySelector("textarea")?.value).toBe("B review note must remain");
});

function LocalizedMemorySurface() { useLocale(); return <ProjectMemoryPanel projectId="project:p" />; }
it("CLOSE-03 an existing memory validation notice follows the display locale", async () => {
  await mount(<LocalizedMemorySurface />); await settle(() => Boolean(container.querySelector("[data-memory-ready]")));
  await click(memoryCard("A").querySelector("button"));
  await click(button(t("memoryEdit.send"), dialog()!));
  expect(dialog()?.textContent).toContain(t("memoryEdit.required", "ko"));
  const before = fixture.calls.length;
  await act(async () => { setLocale("en"); await tick(); });
  observe({ case: "CLOSE-03:locale", oldNotice: dialog()?.textContent?.includes(t("memoryEdit.required", "ko")), newNotice: dialog()?.textContent?.includes(t("memoryEdit.required", "en")), extraRpc: fixture.calls.length - before });
  expect.soft(dialog()?.textContent).toContain(t("memoryEdit.required", "en"));
  expect.soft(dialog()?.textContent).not.toContain(t("memoryEdit.required", "ko"));
  expect(fixture.calls).toHaveLength(before);
  expect(fixture.calls.some(call => call.method === "memory/edit/propose")).toBe(false);
});
