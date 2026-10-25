// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { webcrypto } from "node:crypto";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { rpc, RpcError } from "../api/rpcClient";
import { citations, evidenceResponse, manifest, partial, selection } from "../api/resultEvidenceTestFixture";
import type { ResultSelection } from "../api/resultEvidence";
import { setLocale } from "../i18n/locale";
import { objectList, objectValue } from "../api/presentation";
import type { PackRunResult } from "../types";
import { ResearchDetailPane } from "./ResearchDetailPane";

vi.mock("../api/rpcClient", async original => ({ ...await original<typeof import("../api/rpcClient")>(), rpc: vi.fn() }));
const background = new Set(["hypothesis/review/list", "hypothesis/link/list", "hypothesis/test/result/list", "hypothesis/same/list", "trace/lesson/list"]);
const project = { project_id: selection.scope.projectId } as PackRunResult["project"];
const thread = { thread_id: selection.scope.threadId } as PackRunResult["thread"];
let root: Root | undefined;
let node: HTMLDivElement;
let client: QueryClient;

function resultWith(basis: string[], typed = false): Record<string, unknown> {
  return { ...structuredClone(partial.result), portfolio: { hypotheses: [{
    hypothesis_id: "h1", statement: "Synthetic candidate", uncertainty: "Not confirmed",
    discriminating_tests: [{ test_id: "t1", procedure_candidate: "Compare conditions", expected_if_true: "Different",
      expected_by_hypothesis: [{ hypothesis_id: "h1", expected: "Different", basis,
        ...(typed ? { knowledge_state: "PREDICTED" } : {}) }] }],
  }] } };
}

function replies(result: Record<string, unknown>, modify?: (response: ReturnType<typeof evidenceResponse>) => void) {
  vi.mocked(rpc).mockImplementation(async (method, args) => {
    if (method === "thread/result/read") {
      const response = evidenceResponse(Number(args.selected_evidence_offset ?? 0), args.include_selected_evidence === true, result);
      modify?.(response);
      return response;
    }
    if (!background.has(method)) throw new Error(`Unexpected RPC ${method}`);
    return { operation_id: "", state: "SUCCEEDED", value: { requests: [], links: [], items: [], lessons: [], groups: [], events: [] } };
  });
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(crypto, "subtle", { value: webcrypto.subtle, configurable: true });
  setLocale("ko");
});
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; node?.remove(); client?.clear();
  expect(vi.mocked(rpc).mock.calls.every(([method]) => background.has(method) || method === "thread/result/read")).toBe(true);
  vi.resetAllMocks(); setLocale("ko");
});

async function render(result: Record<string, unknown>, selected: ResultSelection | null = selection) {
  if (!root) {
    node = document.createElement("div"); document.body.append(node); root = createRoot(node);
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  }
  await act(async () => root!.render(<QueryClientProvider client={client}><ResearchDetailPane
    detail={{ kind: "hypotheses", result, historySelection: selected ?? undefined }} project={project} thread={thread} onClose={() => {}}/>
  </QueryClientProvider>));
}
async function settle(check: () => boolean) {
  for (let i = 0; i < 80; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    if (check()) return;
  }
  throw new Error(`Evidence state not reached: ${node.textContent?.slice(-400)}`);
}
async function openBasis(index = 0) {
  const details = node.querySelector<HTMLDetailsElement>(".expected-results")!;
  details.open = true;
  const button = details.querySelectorAll<HTMLButtonElement>("button")[index];
  expect(button).toBeDefined();
  await act(async () => button.click());
}
const evidenceCalls = () => vi.mocked(rpc).mock.calls.filter(([method]) => method === "thread/result/read");
const firstExpected = (result: Record<string, unknown>) => {
  const owner = objectList(objectValue(result.portfolio).hypotheses)[0];
  const test = objectList(owner.discriminating_tests)[0];
  return objectList(test.expected_by_hypothesis)[0];
};

it("opens a v5 row through the existing protected result evidence reader", async () => {
  const result = resultWith([citations[0].span_id], true);
  const row = firstExpected(result);
  Object.assign(row, {grounding: {schema_version: "1.0.0", derivation_role: "EVIDENCE_INFERENCE", rationale: "Synthetic premise."}, binding_availability: "AVAILABLE"});
  replies(result); await render(result); await openBasis();
  await settle(() => node.querySelector(".evidence-item.focused")?.getAttribute("data-span-id") === citations[0].span_id);
  expect(evidenceCalls()).toHaveLength(1);
  expect(node.textContent).toContain("자료에서 추론 · AI 제안");
  expect(node.textContent).not.toContain("EVIDENCE_INFERENCE");
});

it.each(["ko", "en"] as const)("keeps the original source excerpt available through evidence opening (%s)", async language => {
  setLocale(language);
  const text = '# SYNTHETIC DEMO DATA - NOT MEASURED\n- {id: SAMPLE, unit: ratio}\n조건,분자,분모\n비,16,20';
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))))
    .map(byte => byte.toString(16).padStart(2, "0")).join("");
  const target = citations[0];
  const result = resultWith([target.span_id], true);
  const original = structuredClone(result);
  replies(result, response => {
    response.value.manifest.source_basis = { ...response.value.manifest.source_basis, [target.span_id]: `${target.source_version_id}:${hash}` };
    const item = response.value.selected_evidence?.items.find(item => item.span_id === target.span_id);
    if (item?.span) Object.assign(item.span, { exact_text: text, text_sha256: hash });
  });
  await render(result); await openBasis();
  await settle(() => node.querySelector(".evidence-item.focused")?.getAttribute("data-span-id") === target.span_id);
  expect(node.querySelector(".evidence-item.focused")?.textContent).toContain(text);
  expect(result).toEqual(original);
  expect(evidenceCalls()).toHaveLength(1);
});

it("does not offer an unavailable legacy binding as an openable source", async () => {
  const result = resultWith([citations[0].span_id], true);
  const row = firstExpected(result);
  Object.assign(row, {grounding: {schema_version: "1.0.0", derivation_role: "UNASSESSED", rationale: ""}, grounding_projection: "LEGACY_UNASSESSED", binding_availability: "UNAVAILABLE"});
  replies(result); await render(result);
  expect(node.querySelectorAll(".expected-results button")).toHaveLength(0);
  expect(node.textContent).toContain("근거와의 관계는 아직 검토하지 않음");
  expect(node.querySelector(".expected-grounding-role")).toBeNull();
  expect(evidenceCalls()).toHaveLength(0);
});

it.each([false, true])("opens a basis through the saved result and focuses its span (typed=%s)", async typed => {
  const target = citations.at(-1)!;
  const result = resultWith([citations[0].span_id, target.span_id], typed);
  const unchanged = structuredClone(result);
  replies(result); await render(result);
  expect(evidenceCalls()).toHaveLength(0);
  expect(node.textContent).toContain("모델이 낸 제안");
  const buttons = node.querySelectorAll<HTMLButtonElement>(".expected-results button");
  expect(buttons[1]?.textContent).toBe("근거 2 열기");
  expect(buttons[1]?.getAttribute("aria-label")).toBe("가설 1의 근거 2 열기");
  await openBasis(1);
  await settle(() => node.querySelector(".evidence-item.focused")?.getAttribute("data-span-id") === target.span_id);
  expect(node.querySelector(".evidence-item.focused")?.textContent).toContain(target.exact_text);
  expect(evidenceCalls()).toHaveLength(1);
  for (const [, args] of evidenceCalls()) {
    expect(args.project_id).toBe(selection.scope.projectId);
    expect(args.thread_id).toBe(selection.scope.threadId);
    expect(args.request_revision_digest).toBe(selection.scope.requestDigest);
    expect(args.result_revision_digest).toBe(selection.resultDigest);
  }
  expect(result).toEqual(unchanged);
  await openBasis(0);
  await settle(() => node.querySelector(".evidence-item.focused")?.getAttribute("data-span-id") === citations[0].span_id);
  await openBasis(0);
  expect(evidenceCalls()).toHaveLength(1);
});

it("keeps empty-basis rows unconfirmed and adds no open buttons or evidence requests", async () => {
  const result = resultWith([]); replies(result); await render(result);
  expect(node.querySelectorAll(".expected-results button")).toHaveLength(0);
  expect(node.textContent).toContain("자료로 확인되지 않은 예상");
  expect(evidenceCalls()).toHaveLength(0);
});

it.each([false, true])("reports a missing span without substituting project sources (empty manifest=%s)", async empty => {
  const result = resultWith(["span:not-in-this-result"]);
  if (empty) { result.selected_evidence_count = 0; result.selected_evidence_refs = []; }
  replies(result, response => {
    if (!empty) return;
    Object.assign(response.value.manifest, { source_refs: [], source_basis: {} });
    const page = response.value.selected_evidence;
    if (page) { page.items = []; page.selected_count = 0; page.next_offset = null; }
  });
  await render(result); await openBasis();
  await settle(() => Boolean(node.textContent?.includes("이 저장 결과에 포함된 근거가 아니어서 원문을 확인할 수 없습니다.")));
  expect(node.querySelectorAll(".evidence-item.focused")).toHaveLength(0);
});

it("follows pinned pages for a later selected span whose text is unavailable", async () => {
  const result = resultWith([manifest.source_refs[120]]); replies(result);
  await render(result); await openBasis();
  await settle(() => Boolean(node.textContent?.includes("이 근거는 원문을 표시할 수 없습니다")));
  expect(evidenceCalls()).toHaveLength(3);
  expect(evidenceCalls().map(([, args]) => args.selected_evidence_offset)).toEqual([0, 50, 100]);
  expect(node.textContent).not.toContain("이 저장 결과에 포함된 근거가 아니어서");
});

it("uses approved English button and missing-source text", async () => {
  setLocale("en");
  const result = resultWith(["span:absent"]); replies(result); await render(result);
  const button = node.querySelector<HTMLButtonElement>(".expected-results button");
  expect(button?.textContent).toBe("Open evidence 1");
  expect(button?.getAttribute("aria-label")).toBe("Open evidence 1 for Hypothesis 1");
  await openBasis();
  await settle(() => Boolean(node.textContent?.includes("The original text cannot be shown because this evidence is not part of the saved result.")));
});

it.each(["wrong-project", "no-operation", "missing-selection"])("keeps invalid selection unavailable (%s)", async kind => {
  const result = resultWith([citations[0].span_id]); replies(result);
  const selected = kind === "missing-selection" ? null : kind === "wrong-project"
    ? { ...selection, scope: { ...selection.scope, projectId: "other" } } : { ...selection, operationId: "" };
  await render(result, selected); await openBasis();
  await settle(() => Boolean(node.querySelector(".hypothesis-basis-panel [role=alert]")));
  expect(node.querySelectorAll(".evidence-item")).toHaveLength(0);
  expect(evidenceCalls()).toHaveLength(0);
});

it.each(["denied", "identity", "source-version"])("retains the existing protected reader's refusal (%s)", async failure => {
  const result = resultWith([citations[0].span_id]); replies(result, response => {
    if (failure === "denied") throw new RpcError("denied", -32040, {});
    if (failure === "identity") response.value.operation_id = "another-operation";
    else if (response.value.selected_evidence) response.value.selected_evidence.items[0].span!.source_version_id = "different-version";
  });
  await render(result); await openBasis();
  await settle(() => Boolean(node.textContent?.includes("근거를 읽지 못했습니다")));
  expect(node.querySelectorAll(".evidence-item")).toHaveLength(0);
});

it("clears prior focus when result content or selected revision changes", async () => {
  const result = resultWith([citations[0].span_id]); replies(result); await render(result); await openBasis();
  await settle(() => Boolean(node.querySelector(".evidence-item.focused")));
  const count = evidenceCalls().length;
  const changed = { ...result, answer: "A different saved answer" };
  await render(changed);
  expect(node.querySelector(".hypothesis-basis-panel")).toBeNull();
  expect(evidenceCalls()).toHaveLength(count);
  await render(changed, { ...selection, resultDigest: "c".repeat(64) });
  expect(node.querySelector(".hypothesis-basis-panel")).toBeNull();
});

it("does not display a late old response after another result is selected", async () => {
  const result = resultWith([citations[0].span_id]); replies(result);
  const ordinary = vi.mocked(rpc).getMockImplementation()!;
  let resolveOld: ((response: ReturnType<typeof evidenceResponse>) => void) | undefined;
  vi.mocked(rpc).mockImplementation((method, ...args) => method === "thread/result/read"
    ? new Promise(resolve => { resolveOld = resolve; }) : ordinary(method, ...args));
  await render(result); await openBasis();
  await settle(() => resolveOld !== undefined);
  await render({ ...result, answer: "Another result" }, { ...selection, resultDigest: "d".repeat(64) });
  await act(async () => resolveOld!(evidenceResponse(0, true, result)));
  expect(node.querySelector(".hypothesis-basis-panel")).toBeNull();
  expect(node.querySelectorAll(".evidence-item")).toHaveLength(0);
  expect(evidenceCalls()).toHaveLength(1);
});
