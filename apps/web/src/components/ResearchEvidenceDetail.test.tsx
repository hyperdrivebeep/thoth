// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import fixture from "../api/fixtures/qa04-partial-result.json";
import { rpc } from "../api/rpcClient";
import { ResearchResultCard, type ResearchDetail } from "./ResearchResultCard";
import { ResearchDetailPane } from "./ResearchDetailPane";
import type { PackRunResult } from "../types";
import { evidenceResponse, selection, citations } from "../api/resultEvidenceTestFixture";
import { RpcError } from "../api/rpcClient";
import { ResultEvidencePanel } from "./ResultEvidencePanel";
import { webcrypto } from "node:crypto";

vi.mock("../api/rpcClient", async original => ({ ...await original<typeof import("../api/rpcClient")>(), rpc: vi.fn() }));
let root: Root | undefined;
let node: HTMLDivElement;
let client: QueryClient;
const project = { project_id: fixture.scope.projectId } as PackRunResult["project"];
const thread = { thread_id: fixture.scope.threadId } as PackRunResult["thread"];

function AnswerDetailFlow() {
  const [detail, setDetail] = useState<ResearchDetail | null>(null);
  return <><ResearchResultCard result={fixture.result} state="FAILED" historySelection={{ kind: "result", scope: fixture.scope, operationId: fixture.operationId }} onDetail={setDetail}/>
    {detail && <ResearchDetailPane detail={detail} project={project} thread={thread} onClose={() => setDetail(null)}/>}</>;
}

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; node?.remove(); client?.clear(); vi.resetAllMocks();
});

async function settle(check: () => boolean) {
  for (let i = 0; i < 80; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    if (check()) return;
  }
  throw new Error(`Expected evidence state was not rendered: ${node.textContent?.slice(0, 200)}`);
}
async function mount() {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(crypto, "subtle", { value: webcrypto.subtle, configurable: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(<QueryClientProvider client={client}><AnswerDetailFlow/></QueryClientProvider>));
  await act(async () => Array.from(node.querySelectorAll("button")).find(button => button.textContent === "근거 원문")!.click());
}

it("opens the synthetic partial answer's sources and loads 50/50/49 selected entries", async () => {
  vi.mocked(rpc).mockImplementation(async (_method, args) => evidenceResponse(Number(args.selected_evidence_offset ?? 0), args.include_selected_evidence === true));
  await mount();
  await settle(() => Boolean(node.textContent?.includes("전체 149개 중 50개 확인")));
  for (const loaded of [100, 149]) {
    await act(async () => Array.from(node.querySelectorAll("button")).find(button => button.textContent === "다음 근거 보기")!.click());
    await settle(() => Boolean(node.textContent?.includes(`전체 149개 중 ${loaded}개 확인`)));
  }
  expect(node.textContent).toContain(citations[0].exact_text);
  expect(node.textContent).toContain("p.1 · line 1");
  expect(node.textContent).toContain("확인된 원문 25개");
  expect(node.textContent).toContain("직접 인용한 개수와 다를 수 있습니다");
  expect(node.textContent).not.toContain("활성 근거 0개");
  expect(vi.mocked(rpc).mock.calls.map(([method]) => method)).toEqual(Array(4).fill("thread/result/read"));
});

it.each(["denied", "timeout", "basis"])("hides already loaded source bytes after a %s refetch, later timeout and remount", async failure => {
  vi.mocked(rpc).mockImplementation(async (_method, args) => evidenceResponse(Number(args.selected_evidence_offset ?? 0), args.include_selected_evidence === true));
  await mount();
  await settle(() => Boolean(node.textContent?.includes("전체 149개 중 50개 확인")));
  if (failure === "basis") {
    const changed = evidenceResponse();
    changed.value.selected_evidence!.items.find(item => item.span)!.span!.source_version_id = "changed-version";
    vi.mocked(rpc).mockResolvedValue(changed);
  } else vi.mocked(rpc).mockRejectedValue(failure === "denied" ? new RpcError("denied", -32040, {}) : new Error("RPC timed out"));
  await act(async () => { await client.invalidateQueries({ queryKey: ["result-evidence"] }); });
  await settle(() => Boolean(node.textContent?.includes("근거를 읽지 못했습니다")));
  expect(node.querySelectorAll(".evidence-item")).toHaveLength(0);
  expect(node.textContent).not.toContain("활성 근거 0개");
  expect(JSON.stringify(client.getQueriesData({ queryKey: ["result-evidence"] }))).not.toContain(citations[0].exact_text);
  vi.mocked(rpc).mockRejectedValue(new Error("RPC timed out"));
  await act(async () => { await client.invalidateQueries({ queryKey: ["result-evidence"] }); });
  expect(node.querySelectorAll(".evidence-item")).toHaveLength(0);
  await act(async () => root!.unmount());
  root = createRoot(node);
  await act(async () => root!.render(<QueryClientProvider client={client}><AnswerDetailFlow/></QueryClientProvider>));
  await act(async () => Array.from(node.querySelectorAll("button")).find(button => button.textContent === "근거 원문")!.click());
  await settle(() => Boolean(node.textContent?.includes("근거를 읽지 못했습니다")));
  expect(node.querySelectorAll(".evidence-item")).toHaveLength(0);
});

it("does not restore rejected source bytes when a later refetch is cancelled", async () => {
  vi.mocked(rpc).mockImplementation(async (_method, args) => evidenceResponse(Number(args.selected_evidence_offset ?? 0), args.include_selected_evidence === true));
  await mount(); await settle(() => Boolean(node.textContent?.includes("전체 149개 중 50개 확인")));
  vi.mocked(rpc).mockRejectedValue(new RpcError("denied", -32040, {}));
  await act(async () => { await client.invalidateQueries({ queryKey: ["result-evidence"] }); });
  await settle(() => Boolean(node.textContent?.includes("근거를 읽지 못했습니다")));
  vi.mocked(rpc).mockImplementation(() => new Promise(() => {}));
  await act(async () => { void client.invalidateQueries({ queryKey: ["result-evidence"] }); });
  await act(async () => { await client.cancelQueries({ queryKey: ["result-evidence"] }); });
  expect(node.querySelectorAll(".evidence-item")).toHaveLength(0);
  expect(node.textContent).toContain("근거를 읽지 못했습니다");
});

it("hides pages when the current caller scope changes during pagination", async () => {
  vi.mocked(rpc).mockImplementation(async (_method, args) => {
    const response = evidenceResponse(Number(args.selected_evidence_offset ?? 0), args.include_selected_evidence === true);
    if (response.value.selected_evidence && Number(args.selected_evidence_offset) === 50) response.value.selected_evidence.actor_scope_digest = "b".repeat(64);
    return response;
  });
  await mount(); await settle(() => Boolean(node.textContent?.includes("전체 149개 중 50개 확인")));
  await act(async () => Array.from(node.querySelectorAll("button")).find(button => button.textContent === "다음 근거 보기")!.click());
  await settle(() => Boolean(node.textContent?.includes("접근 범위 또는 기록 연결이 바뀌었습니다")));
  expect(node.querySelectorAll(".evidence-item")).toHaveLength(0);
});

it("distinguishes missing historical basis from a genuinely empty reference set", async () => {
  vi.mocked(rpc).mockImplementation(async (_method, args) => {
    const response = evidenceResponse(0, args.include_selected_evidence === true);
    response.value.selected_evidence?.items.forEach(item => { item.span = null; item.availability = "UNKNOWN_BASIS"; });
    return response;
  });
  await mount(); await settle(() => Boolean(node.textContent?.includes("저장 당시의 자료 기준을 확인할 수 없어")));
  expect(node.textContent).toContain("이 답변에 선택된 근거 149개");
  expect(node.textContent).not.toContain("선택된 근거가 없습니다");
});

it("ignores a late response for the previously selected operation", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(crypto, "subtle", { value: webcrypto.subtle, configurable: true });
  let completeOld!: (response: ReturnType<typeof evidenceResponse>) => void;
  vi.mocked(rpc).mockImplementationOnce(() => new Promise(resolve => { completeOld = resolve; }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(<QueryClientProvider client={client}><ResultEvidencePanel selection={selection} result={fixture.result}/></QueryClientProvider>));
  vi.mocked(rpc).mockRejectedValue(new RpcError("second operation denied", -32040, {}));
  await act(async () => root!.render(<QueryClientProvider client={client}><ResultEvidencePanel selection={{ ...selection, operationId: "other-op" }} result={fixture.result}/></QueryClientProvider>));
  await settle(() => Boolean(node.textContent?.includes("second operation denied")));
  await act(async () => completeOld(evidenceResponse()));
  await settle(() => Boolean(node.textContent?.includes("second operation denied")));
  expect(node.querySelectorAll(".evidence-item")).toHaveLength(0);
});

it("reads the saved partial answer's evidence when its button opens instead of reporting zero", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(rpc).mockImplementation(() => new Promise(() => {}));
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(<QueryClientProvider client={client}><AnswerDetailFlow/></QueryClientProvider>));
  expect(vi.mocked(rpc)).not.toHaveBeenCalled();
  await act(async () => Array.from(node.querySelectorAll("button")).find(button => button.textContent === "근거 원문")!.click());
  const panel = node.querySelector('[aria-label="원문 근거"]')!;
  expect(panel.textContent).toContain("근거를 읽는 중");
  expect(panel.textContent).not.toContain("활성 근거 0개");
  expect(panel.textContent).not.toContain("자료를 연결하거나");
});
