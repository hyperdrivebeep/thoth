// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import fixture from "../api/fixtures/qa04-partial-result.json";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { rpc } from "../api/rpcClient";
import { ResearchResultCard, type ResearchDetail } from "./ResearchResultCard";
import { ResearchDetailPane } from "./ResearchDetailPane";
import type { PackRunResult } from "../types";
import { evidenceResponse, manifest } from "../api/resultEvidenceTestFixture";
import { webcrypto } from "node:crypto";

vi.mock("../api/rpcClient", async original => ({ ...await original<typeof import("../api/rpcClient")>(), rpc: vi.fn() }));
let root: Root | undefined;
let node: HTMLDivElement;
let client: QueryClient;
const project = { project_id: fixture.scope.projectId } as PackRunResult["project"];
const thread = { thread_id: fixture.scope.threadId } as PackRunResult["thread"];
const scrolled = vi.fn();

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; node?.remove(); client?.clear(); vi.resetAllMocks(); scrolled.mockReset();
});

function Flow({ result }: { result: Record<string, unknown> }) {
  const [detail, setDetail] = useState<ResearchDetail | null>(null);
  return <><ResearchResultCard result={result} state="SUCCEEDED"
    historySelection={{ kind: "result", scope: fixture.scope, operationId: fixture.operationId }} onDetail={setDetail}/>
    {detail && <ResearchDetailPane detail={detail} project={project} thread={thread} onClose={() => setDetail(null)}/>}</>;
}
async function mount(node2: React.ReactElement) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(crypto, "subtle", { value: webcrypto.subtle, configurable: true });
  Element.prototype.scrollIntoView = scrolled;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  node = document.createElement("div"); document.body.append(node); root = createRoot(node);
  await act(async () => root!.render(<QueryClientProvider client={client}>{node2}</QueryClientProvider>));
}
async function settle(check: () => boolean) {
  for (let i = 0; i < 200; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    if (check()) return;
  }
  throw new Error(`not rendered: ${node.textContent?.slice(-500)}`);
}
const cites = () => Array.from(node.querySelectorAll<HTMLButtonElement>("button.answer-citation"));

it("shows numbered citations and bold text instead of internal ids and asterisks", async () => {
  const opened: ResearchDetail[] = [];
  await mount(<ResearchResultCard result={{ answer: excerpt.answer }} state="SUCCEEDED" onDetail={detail => opened.push(detail)}
    historySelection={{ kind: "result", scope: fixture.scope, operationId: fixture.operationId }}/>);
  const text = node.querySelector(".answer-text")!.textContent!;
  expect(text).not.toContain("span:");
  expect(text).not.toContain("**");
  expect(text).toContain("[1]");
  expect(node.querySelector(".answer-text strong")?.textContent).toContain("1세부");
  const first = cites()[0];
  expect(first.textContent).toBe("[1]");
  await act(async () => first.click());
  expect(opened).toHaveLength(1);
  expect(opened[0].kind).toBe("evidence");
  expect(opened[0].focusSpanId).toBe("span:1580e7b4-64bb-4eb0-99c2-5a8fc6113f70");
  expect(opened[0].citations?.["span:1580e7b4-64bb-4eb0-99c2-5a8fc6113f70"]).toBe(1);
});

it("keeps one number for a source cited twice", async () => {
  await mount(<ResearchResultCard result={{ answer: "가 (span:aaaa1111) 나 (span:bbbb2222) 다 (span:aaaa1111)" }} state="SUCCEEDED" onDetail={() => {}}/>);
  expect(cites().map(item => item.textContent)).toEqual(["[1]", "[2]", "[1]"]);
});

function serve(result: Record<string, unknown>) {
  vi.mocked(rpc).mockImplementation(async (_method, args) => evidenceResponse(Number(args.selected_evidence_offset ?? 0), args.include_selected_evidence === true, result));
}
const cited = (id: string) => ({ ...fixture.result, answer: `이 문장은 근거에 기대어 있습니다 (${id}).` });

it("moves to the cited source card in the evidence panel and highlights it", async () => {
  const target = manifest.source_refs[10];
  const result = cited(target);
  serve(result);
  await mount(<Flow result={result}/>);
  await act(async () => cites()[0].click());
  await settle(() => Boolean(node.querySelector('.evidence-item[data-span-id="' + target + '"]')));
  const card = node.querySelector<HTMLElement>('.evidence-item[data-span-id="' + target + '"]')!;
  expect(card.classList.contains("focused")).toBe(true);
  expect(card.textContent).toContain("[1]");
  expect(scrolled).toHaveBeenCalled();
  expect(node.textContent).not.toContain("이 근거를 목록에서 찾지 못했습니다");
});

it("reads later pages to find a cited source whose original text cannot be shown", async () => {
  const result = cited(manifest.source_refs[120]);
  serve(result);
  await mount(<Flow result={result}/>);
  await act(async () => cites()[0].click());
  await settle(() => Boolean(node.textContent?.includes("이 근거는 원문을 표시할 수 없습니다")));
  expect(node.textContent).toContain("전체 149개 중 149개 확인");
});

it("says so when the cited source is not in the answer's evidence list", async () => {
  const result = cited("span:no-such-source-0001");
  serve(result);
  await mount(<Flow result={result}/>);
  await act(async () => cites()[0].click());
  await settle(() => Boolean(node.textContent?.includes("이 근거를 목록에서 찾지 못했습니다")));
  expect(node.textContent).toContain("전체 149개 중 149개 확인");
});
