// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { message } from "../i18n/formatMessages";
import { setLocale, t } from "../i18n/locale";
import type { Verdict } from "../api/trace";
import type { HypothesisLink } from "../api/hypothesisLink";
import { ActionEffort } from "./ActionEffort";
import { ClosureRecorder } from "./ClosureRecorder";
import { ExecutionHoldNotice } from "./ExecutionHoldNotice";
import { LinkRecheckDialog } from "./LinkRecheckDialog";
import { ProjectMemoryPanel } from "./ProjectMemoryPanel";
import { RefutationConditionsPanel } from "./RefutationConditionsPanel";
import { ReviewDialog } from "./ReviewDialog";
import { SameHypothesis } from "./SameHypothesis";
import { TestResultRecorder } from "./TestResultRecorder";
import { TraceConfirmDialog } from "./TraceConfirmDialog";
import { TracePage } from "./TracePage";
import demo from "./traceDemo.fixture.json";

const fixture = vi.hoisted(() => ({ gate: null as Promise<void> | null, method: "", calls: [] as string[] }));
vi.mock("../api/rpcClient", async importOriginal => ({ ...await importOriginal<typeof import("../api/rpcClient")>(),
  rpc: async (method: string) => {
    fixture.calls.push(method);
    if (method === fixture.method && fixture.gate) await fixture.gate;
    let value: unknown = {};
    if (method === "action/read") value = { action: { revision_digest: "a".repeat(64), generation_details: { effort_estimates: [] } }, research_running: false };
    else if (method === "trace/read") value = demo.phase1;
    else if (method === "trace/confirm") value = demo.afterConfirm;
    else if (method === "trace/closure/list") value = { closures: [] };
    else if (method === "memory/settings/read") value = { memory_injection: true, settings_digest: null };
    else if (method === "memory/revision/list") value = { revisions: [{ memory_id: "memory:a", memory_revision_id: "revision:a", revision_digest: "a".repeat(64), parent_revision_digest: null, owner_revision_ref: "owner:a", kind: "LESSON", summary: "Synthetic memory", assertion: "Synthetic memory", source_ref: null, evidence_count: 0, evidence_refs: [], transition: "COMMIT", support_status: "SUPPORTED", authority_status: "AUTHORITATIVE", cutoff_valid: true, is_latest: true, not_recalled_because: null, created_at: "2026-10-01T00:00:00Z" }], counts: { total: 1, recallable: 1, not_recalled: 0 } };
    else if (method === "memory/edit/propose") value = { transition: "COMMIT", revision: { support_status: "SUPPORTED" } };
    else if (!["action/revise", "trace/closure/record", "hypothesis/test/result/record", "hypothesis/refutation/record", "hypothesis/same/record", "hypothesis/link/list"].includes(method)) throw new Error(`UNEXPECTED_FAKE_RPC:${method}`);
    return { value, operation_id: "synthetic", state: "SUCCEEDED" };
  },
}));

const verdict = { ...demo.phase1.verdicts[0], subject_id: "synthetic:A", revision_digest: "a".repeat(64) } as unknown as Verdict;
const link: HypothesisLink = { hypothesis_id: "h2", hypothesis_revision_digest: "a".repeat(64), statement: "Synthetic other hypothesis", subject_kind: "CRITERION", subject_id: "c", subject_title: "Synthetic target", state: "CURRENT", change: null, link_state: "FAIL_COMPUTED", current_state: "FAIL_COMPUTED", current_verdict_revision: "b".repeat(64), recheck: null };
let root: Root | undefined, container: HTMLDivElement, client: QueryClient;
let release: (() => void) | undefined;
const tick = (ms = 20) => new Promise(resolve => setTimeout(resolve, ms));
const dialog = () => document.body.querySelector<HTMLElement>(".bp6-dialog");
const button = (text: string, scope: ParentNode = document.body) => [...scope.querySelectorAll<HTMLButtonElement>("button")].find(node => (node.getAttribute("aria-label") ?? node.textContent?.trim()) === text);
async function click(node: HTMLElement | undefined | null) { expect(node).toBeTruthy(); await act(async () => { node!.click(); await tick(); }); }
async function fill(node: HTMLInputElement | HTMLTextAreaElement | null, value: string) {
  expect(node).toBeTruthy();
  await act(async () => { const proto = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(node, value); node!.dispatchEvent(new Event("input", { bubbles: true })); });
}
async function select(node: HTMLSelectElement, value: string) { await act(async () => { node.value = value; node.dispatchEvent(new Event("change", { bubbles: true })); }); }
async function settle(check: () => boolean) { for (let i = 0; i < 65; i++) { await act(async () => { await tick(); }); if (check()) return; } throw new Error("NOT_TRIGGERED"); }
async function mount(node: ReactNode) { container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => { root!.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>); }); await settle(() => client.isFetching() === 0); }
beforeEach(() => { Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); fixture.gate = null; fixture.method = ""; fixture.calls = []; release = undefined; setLocale("ko");
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("REAL_FETCH_FORBIDDEN"); })); });
afterEach(async () => { if (root) await act(async () => { root!.unmount(); }); root = undefined; release?.(); await act(async () => { await tick(); }); client?.clear(); container?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); setLocale("ko"); });
function hold(method: string) { fixture.method = method; fixture.gate = new Promise<void>(resolve => { release = resolve; }); }
async function complete() { await act(async () => { release!(); await tick(); }); await settle(() => client.isMutating() === 0 && client.isFetching() === 0); await act(async () => { await tick(350); }); }

function ClosureSurface() {
  const [target, setTarget] = useState<Verdict | null>(verdict);
  return <><button onClick={() => setTarget({ ...verdict, subject_id: "synthetic:B", revision_digest: "b".repeat(64) })}>Open B</button>
    <ClosureRecorder projectId="p" verdict={target} title={target?.subject_id ?? ""} onClose={() => setTarget(null)} /></>;
}

it.each(["ActionEffort", "ClosureRecorder", "TestResultRecorder", "RefutationConditionsPanel", "SameHypothesis", "TracePage"])("keeps newer draft when %s's previous mutation finishes", async owner => {
  if (owner === "ActionEffort") {
    await mount(<ActionEffort projectId="p" actionId="a" initial={[]} />); await click(button(t("effort.open")));
    await select(container.querySelector("select")!, "HIGH"); await fill(container.querySelector(".effort-editor input"), "A basis");
    hold("action/revise"); await click(button(t("effort.saveHuman"))); await settle(() => fixture.calls.includes(fixture.method));
    await fill(container.querySelector(".effort-editor input"), "B basis"); await complete();
    expect(container.querySelector<HTMLInputElement>(".effort-editor input")?.value).toBe("B basis");
  } else if (owner === "ClosureRecorder") {
    await mount(<ClosureSurface />); await click(dialog()!.querySelector<HTMLInputElement>('input[value="FIX_APPLIED"]')); await fill(dialog()!.querySelector('input[aria-label]'), "A basis");
    hold("trace/closure/record"); await click(button(t("testResult.save"), dialog()!)); await settle(() => fixture.calls.includes(fixture.method));
    await click(button(t("hold.close"), dialog()!)); await settle(() => !dialog()); await click(button("Open B"));
    await fill(dialog()!.querySelector('input[aria-label]'), "B basis"); await complete();
    expect(dialog()?.textContent).toContain("synthetic:B"); expect(dialog()?.querySelector<HTMLInputElement>('input[aria-label]')?.value).toBe("B basis");
  } else if (owner === "TestResultRecorder") {
    await mount(<TestResultRecorder projectId="p" hypothesisId="h" testId="test" />); await click(button(t("testResult.record")));
    await click(dialog()!.querySelector<HTMLInputElement>('input[value="THIS_HYPOTHESIS"]')); await fill(dialog()!.querySelector("textarea"), "A observation");
    hold("hypothesis/test/result/record"); await click(button(t("testResult.save"), dialog()!)); await settle(() => fixture.calls.includes(fixture.method));
    await fill(dialog()!.querySelector("textarea"), "B observation"); await complete(); expect(dialog()?.querySelector("textarea")?.value).toBe("B observation");
  } else if (owner === "RefutationConditionsPanel") {
    await mount(<RefutationConditionsPanel projectId="p" hypothesisId="h" />); await click(button(t("refutation.record")));
    await fill(dialog()!.querySelector("textarea"), "A condition"); hold("hypothesis/refutation/record"); await click(button(t("testResult.save"), dialog()!)); await settle(() => fixture.calls.includes(fixture.method));
    await fill(dialog()!.querySelector("textarea"), "B condition"); await complete(); expect(dialog()?.querySelector("textarea")?.value).toBe("B condition");
  } else if (owner === "SameHypothesis") {
    await mount(<SameHypothesis projectId="p" hypothesisId="h1" same={{ groups: [], pairs: [], members: {}, events: [] }} candidates={[link]} />);
    await click(button("다른 조사의 같은 가설로 표시")); await click(container.querySelector<HTMLInputElement>('input[type="radio"]')); await fill(container.querySelector("textarea"), "A note");
    hold("hypothesis/same/record"); await click(button(t("sameHypothesis.apply"))); await settle(() => fixture.calls.includes(fixture.method));
    await fill(container.querySelector("textarea"), "B note"); await complete(); expect(container.querySelector("textarea")?.value).toBe("B note");
  } else {
    await mount(<TracePage projectId="p" />);
    const confirms = () => [...container.querySelectorAll<HTMLButtonElement>("button")].filter(node => node.textContent === t("tracePage.confirm"));
    await click(confirms()[0]); await fill(dialog()!.querySelector("textarea"), "A reason"); hold("trace/confirm"); await click(button(t("traceConfirm.record"), dialog()!)); await settle(() => fixture.calls.includes(fixture.method));
    await click(button(t("hold.close"), dialog()!)); await settle(() => !dialog()); await click(confirms()[1]); await fill(dialog()!.querySelector("textarea"), "B reason");
    await complete(); expect(dialog()?.querySelector("textarea")?.value).toBe("B reason");
  }
});

it.each(["ActionEffort", "ClosureRecorder", "ExecutionHoldNotice", "LinkRecheckDialog", "ProjectMemoryPanel", "RefutationConditionsPanel", "ReviewDialog", "TestResultRecorder", "TraceConfirmDialog", "TracePage"])("renders %s's stored notice in the current locale", async owner => {
  let expected: () => string;
  if (owner === "ActionEffort") {
    await mount(<ActionEffort projectId="p" actionId="a" initial={[]} />); await click(button(t("effort.open"))); await click(button(t("effort.saveHuman"))); expected = () => t("effort.choose");
  } else if (owner === "ClosureRecorder") {
    await mount(<ClosureSurface />); await click(button(t("testResult.save"), dialog()!)); expected = () => t("closure.choose");
  } else if (owner === "ExecutionHoldNotice") {
    await mount(<ExecutionHoldNotice projectId="p" causeRef="cause" hold={{ schema_version: "1.0.0", reason_code: "RESEARCH_TEST_RESULT_RECONCILIATION_REQUIRED", plan_execution_id: "e", execution_revision: 1, attempt_id: "a", attempt_state: "SUCCEEDED", input_difference: null, exits: ["CLEAR_AND_RERUN"] }} />);
    await click(button(t("hold.clearAndRerun"))); await click(button(t("hold.clear"), dialog()!)); expected = () => t("hold.reasonNeeded");
  } else if (owner === "LinkRecheckDialog") {
    await mount(<LinkRecheckDialog target={link} siblings={[]} pending={false} error={null} onClose={() => {}} onSubmit={() => {}} />); await click(button(t("traceConfirm.record"), dialog()!)); expected = () => t("recheck.reasonNeeded");
  } else if (owner === "ProjectMemoryPanel") {
    await mount(<ProjectMemoryPanel projectId="p" />); await click(container.querySelector<HTMLButtonElement>(".memory-card button")); await fill(dialog()!.querySelectorAll("textarea")[0], "Synthetic correction"); await fill(dialog()!.querySelectorAll("textarea")[1], "Synthetic reason");
    await click(button(t("memoryEdit.send"), dialog()!)); await settle(() => client.isMutating() === 0); expected = () => t("memoryEdit.applied");
  } else if (owner === "RefutationConditionsPanel") {
    await mount(<RefutationConditionsPanel projectId="p" hypothesisId="h" />); await click(button(t("refutation.record"))); await fill(dialog()!.querySelector("textarea"), Array.from({ length: 11 }, (_, i) => `condition${i}`).join("\n"));
    await click(button(t("testResult.save"), dialog()!)); expected = () => message("maxRefutations", 10);
  } else if (owner === "ReviewDialog") {
    await mount(<ReviewDialog target={{ hypothesisId: "h", statement: "Synthetic", evidenceRef: null, evidenceLabel: "" }} pending={false} error={null} onClose={() => {}} onSubmit={() => {}} />); await click(button(t("reviewDialog.send"), dialog()!)); expected = () => t("reviewDialog.required");
  } else if (owner === "TestResultRecorder") {
    await mount(<TestResultRecorder projectId="p" hypothesisId="h" testId="t" />); await click(button(t("testResult.record"))); await click(button(t("testResult.save"), dialog()!)); expected = () => t("testResult.choose");
  } else if (owner === "TraceConfirmDialog") {
    await mount(<TraceConfirmDialog verdict={verdict} title="Synthetic" pending={false} error={null} onClose={() => {}} onSubmit={() => {}} />); await click(button(t("traceConfirm.record"), dialog()!)); expected = () => t("traceConfirm.reasonNeeded");
  } else {
    await mount(<TracePage projectId="p" />); await click([...container.querySelectorAll<HTMLButtonElement>("button")].find(node => node.textContent === t("tracePage.confirm")));
    await fill(dialog()!.querySelector("textarea"), "Synthetic rationale"); await click(button(t("traceConfirm.record"), dialog()!)); await settle(() => client.isMutating() === 0); expected = () => t("tracePage.confirmed");
  }
  const old = expected(); expect(document.body.textContent).toContain(old); const count = fixture.calls.length;
  await act(async () => { setLocale("en"); await tick(); });
  expect(document.body.textContent).toContain(expected()); expect(document.body.textContent).not.toContain(old); expect(fixture.calls).toHaveLength(count);
});
