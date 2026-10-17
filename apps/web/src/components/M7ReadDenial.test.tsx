// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { isReadAccessDenied } from "../api/readAccess";
import { RpcError, RpcTransportError } from "../api/rpcClient";
import { setLocale, t } from "../i18n/locale";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { ActionEffort } from "./ActionEffort";
import { ApprovalSection } from "./ApprovalSection";
import { HypothesisCompareLive } from "./HypothesisCompareLive";
import { ProjectWebSetup } from "./ProjectWebSetup";
import { TraceEvidenceDialog } from "./TraceEvidenceDialog";
import { ProjectReviewList } from "./history/ProjectReviewList";
import { ResearchHistoryWorkspace } from "./history/ResearchHistoryWorkspace";
import { useHypothesisLinks } from "./useHypothesisLinks";
import { useDiscrimination, useLessons, useSameHypotheses, useTraceClosures } from "./useJudgmentRecords";

const MARKER = "SYNTHETIC_READ_CONTENT";
const fixture = vi.hoisted(() => ({ method: "", phase: "ok" as "ok" | "network" | "denied", lastError: null as Error | null }));
const hypothesisId = excerpt.portfolio.hypotheses[0].hypothesis_id;
const currentness = { state: "CURRENT", reasons: [], execution_eligible: false };
const capability = { restore: "READ_ONLY", apply_ready: false, preview_supported: false, reason_codes: [] };
const coverage = { visibility: "AUTHORIZED_SUBSET", association: "EXACT", scan: "COMPLETE_PAGE", reasons: [] };
const link = { hypothesis_id: "h", hypothesis_revision_digest: "a".repeat(64), statement: MARKER, subject_kind: "CRITERION", subject_id: "c", subject_title: "Synthetic", state: "CURRENT", change: null, link_state: "FAIL_COMPUTED", current_state: "FAIL_COMPUTED", current_verdict_revision: "b".repeat(64), recheck: null };
vi.mock("../api/rpcClient", async importOriginal => {
  const actual = await importOriginal<typeof import("../api/rpcClient")>();
  return { ...actual, rpc: async (method: string) => {
    if (method === fixture.method && fixture.phase !== "ok") {
      fixture.lastError = fixture.phase === "denied" ? new actual.RpcError("Synthetic denied", -32040, { reason_code: "RESOURCE_ACCESS_DENIED" }) : new actual.RpcTransportError("Synthetic network", "NETWORK");
      throw fixture.lastError;
    }
    let value: unknown;
    if (method === "evidence/list") value = { evidence: [{ span_id: "span:x", artifact_id: "artifact:x", source_version_id: "source:x", exact_text: MARKER, authority_state: "AUTHORITATIVE", cutoff_state: "ELIGIBLE", verification_state: "UNVERIFIED", support_state: "SUPPORTED", locator: { line: 1 } }] };
    else if (method === "project/review/list") value = { project_id: "p", items: [{ item_id: "i", project_id: "p", thread_id: "t", request_revision_digest: "a".repeat(64), result_revision_digest: null, title: MARKER, priority: "HIGH", reason_codes: [], currentness, next_user_action: { action_type: "REVIEW_CURRENTNESS", label: "Synthetic", reason_codes: [], requires_permission: false, target: null, basis: {} } }], next_cursor: null, coverage: "COMPLETE_PAGE", unread_supported: false, assignment_supported: false };
    else if (method === "hypothesis/review/list") value = { requests: [{ request_id: "r", hypothesis_id: hypothesisId, evidence_ref: null, status: "REVIEWING", instruction_state: "ACCEPTED", instruction_status: "ACCEPTED_RUNNING", instruction_failure: null, reason_codes: ["EVIDENCE_INTERPRETATION"], note: "", hypothesis_revision_digest: "a".repeat(64), added_evidence_refs: [], created_at: "2026-10-01T00:00:00Z", resolution: null }] };
    else if (method === "hypothesis/link/list") value = { trace_digest: null, links: [link], reason_distribution: { total: 1, flipped: 0, by_reason: {} } };
    else if (method === "hypothesis/test/result/list") value = { items: [{ hypothesis_id: "h", results: [], result_history_count: 0, refutation_conditions: [MARKER], conditions_history_count: 1, elimination: null, standing: "NONE" }] };
    else if (method === "hypothesis/same/list") value = { groups: [["h", "other"]], pairs: [["h", "other"]], members: { h: { statement: MARKER } }, events: [] };
    else if (method === "trace/lesson/list") value = { lessons: [{ lesson_id: "l", kind: "TEST_RESULT", outcome: MARKER, state: "SAME_CONDITION", subject_kind: "CRITERION", subject_id: "c", hypothesis_id: "h", test_id: null, actor_id: "synthetic", created_at: "2026-10-01T00:00:00Z", detail: {} }] };
    else if (method === "trace/closure/list") value = { closures: [{ subject_kind: "CRITERION", subject_id: "c", current_state: "HOLD_NO_RESULT", effect_confirmed: false, effect_verdict_revision: null, verdict_changed_since: false, events: [{ event_id: "e", subject_kind: "CRITERION", subject_id: "c", kind: "FIX_APPLIED", basis_ref: "synthetic", note: MARKER, scope: null, verdict_revision: "a".repeat(64), verdict_digest: "b".repeat(64), verdict_state: "HOLD_NO_RESULT", actor_id: "synthetic", created_at: "2026-10-01T00:00:00Z" }] }] };
    else if (method === "action/read") value = { action: { revision_digest: "a".repeat(64), generation_details: { effort_estimates: [{ dimension: "TIME", band: "MEDIUM", estimator_type: "AI", estimator_ref: "ACTION_PLANNER", basis_text: MARKER, assumptions: [], created_at: "2026-10-01T00:00:00Z" }] } }, research_running: false };
    else if (method === "action/plan/read") value = { plan: { revision_digest: "a".repeat(64) }, authorizations: [{ authorization_id: "auth", step_id: "step", created_at: "2026-10-01T00:00:00Z" }] };
    else if (method === "action/authorization/read") value = { authorization: { authorization_id: "auth", step_id: "step", state: "PENDING", material_changes: [], payload: {}, revision_digest: "a".repeat(64), created_at: "2026-10-01T00:00:00Z", predecessor_output_digests: [], target_baseline_digests: [], policy_version: "1" }, effective_state: "PENDING", review_needed: false, payload_current: true };
    else if (method === "project/read") value = { revision: 1, policy: { payload: { public_web: { enabled: true, preferred_hosts: [MARKER] } } } };
    else if (method === "workspace/setup/read") value = { internet_consent: "ALLOWED", internet_grant_id: "grant:synthetic" };
    else if (method === "revision/timeline/read") value = { contract_version: 2, items: [{ item_id: "i", kind: "REVISION", occurred_at: "2026-10-01T00:00:00Z", record_ref: { owner_kind: "SEMANTIC_REVISION", project_id: "p", immutable_id: "rev", revision_digest: "a".repeat(64) }, scope_links: [], association: "UNATTRIBUTED", head_membership: "CURRENT", title: MARKER, availability: "AVAILABLE", currentness, capability }], coverage, next_cursor: null, actor_scope_digest: "a".repeat(64), capability };
    else throw new Error(`UNEXPECTED_FAKE_RPC:${method}`);
    return { value, operation_id: "synthetic", state: "SUCCEEDED" };
  } };
});

function Links() { return <output>{JSON.stringify(useHypothesisLinks("p"))}</output>; }
function Discrimination() { return <output>{JSON.stringify(useDiscrimination("p"))}</output>; }
function Same() { return <output>{JSON.stringify(useSameHypotheses("p"))}</output>; }
function Lessons() { return <output>{JSON.stringify(useLessons("p"))}</output>; }
function Closures() { return <output>{JSON.stringify(useTraceClosures("p"))}</output>; }
type Case = { name: string; method: string; node: ReactNode; shown?: () => boolean };
const cases: Case[] = [
  { name: "TraceEvidenceDialog", method: "evidence/list", node: <TraceEvidenceDialog projectId="p" spanRef="span:x" onClose={() => {}} /> },
  { name: "ProjectReviewList", method: "project/review/list", node: <ProjectReviewList projectId="p" onSelect={() => {}} /> },
  { name: "review requests", method: "hypothesis/review/list", node: <HypothesisCompareLive projectId="p" threadId="t" result={excerpt} />, shown: () => Boolean(document.body.textContent?.includes(t("reviewStatus.running"))) },
  { name: "hypothesis links", method: "hypothesis/link/list", node: <Links /> },
  { name: "discrimination", method: "hypothesis/test/result/list", node: <Discrimination /> },
  { name: "same hypotheses", method: "hypothesis/same/list", node: <Same /> },
  { name: "lessons", method: "trace/lesson/list", node: <Lessons /> },
  { name: "closures", method: "trace/closure/list", node: <Closures /> },
  { name: "ActionEffort", method: "action/read", node: <ActionEffort projectId="p" actionId="a" initial={[]} /> },
  { name: "ApprovalSection", method: "action/plan/read", node: <ApprovalSection projectId="p" planId="plan" />, shown: () => Boolean(document.querySelector(".approval-card")) },
  { name: "project web settings", method: "project/read", node: <ProjectWebSetup projectId="p" /> },
  { name: "workspace grant", method: "workspace/setup/read", node: <ProjectWebSetup projectId="p" /> },
  { name: "history timeline", method: "revision/timeline/read", node: <ResearchHistoryWorkspace projectId="p" kinds={["REVISION"]} /> },
];
let root: Root | undefined, container: HTMLDivElement, client: QueryClient;
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
async function settle(check: () => boolean) { for (let i = 0; i < 70; i++) { await act(async () => { await tick(); }); if (check()) return; } throw new Error("NOT_TRIGGERED"); }
beforeEach(() => { Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); fixture.phase = "ok"; fixture.method = ""; fixture.lastError = null; setLocale("ko"); vi.stubGlobal("fetch", vi.fn(() => { throw new Error("REAL_FETCH_FORBIDDEN"); })); });
afterEach(async () => { if (root) await act(async () => { root!.unmount(); await tick(); }); root = undefined; client?.clear(); container?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(cases)("fences $name until an actual read succeeds, preserving transient errors", async row => {
  fixture.method = row.method;
  const shown = row.shown ?? (() => Boolean(document.body.textContent?.includes(MARKER)));
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(["unrelated", "p"], "independent");
  await act(async () => { root!.render(<QueryClientProvider client={client}>{row.node}</QueryClientProvider>); });
  await settle(() => shown() && client.isFetching() === 0);
  const original = new Map(client.getQueryCache().getAll().map(query => [query.queryHash, query.state.data]));
  fixture.phase = "network";
  await act(async () => { await client.refetchQueries({ type: "active" }); });
  expect(shown()).toBe(true);
  fixture.phase = "denied";
  await act(async () => { await client.refetchQueries({ type: "active" }); });
  await settle(() => !shown());
  const deniedQuery = client.getQueryCache().getAll().find(query => query.state.error === fixture.lastError);
  expect(deniedQuery).toBeTruthy();
  fixture.phase = "network";
  await act(async () => { await client.refetchQueries({ type: "active" }); });
  expect(shown()).toBe(false);
  await act(async () => { client.setQueryData(deniedQuery!.queryKey, original.get(deniedQuery!.queryHash)); });
  expect(shown()).toBe(false); // A manual cache success is not a successful authorized read.
  expect(client.getQueryData(["unrelated", "p"])).toBe("independent");
  fixture.phase = "ok";
  await act(async () => { await client.refetchQueries({ type: "active" }); });
  await settle(shown);
});

it.each([
  [new RpcError("synthetic", -32040, {}), true],
  [new RpcError("synthetic", -32030, { reason_code: "RESOURCE_ACCESS_DENIED" }), true],
  [new RpcTransportError("synthetic", "HTTP", 401), true],
  [new RpcTransportError("synthetic", "HTTP", 403), true],
  [new RpcTransportError("synthetic", "NETWORK"), false],
  [new RpcTransportError("synthetic", "TIMEOUT"), false],
  [new RpcTransportError("synthetic", "HTTP", 503), false],
  [new Error("RESOURCE_ACCESS_DENIED translated prose is not a typed reason"), false],
] as const)("classifies typed read refusal %# without message matching", (error, denied) => {
  expect(isReadAccessDenied(error)).toBe(denied);
});
