// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { createHistoryFixtureTransport, historyFixtureIds as ids } from "../../api/historyTestFixture";
import { ResearchHistoryWorkspace } from "./ResearchHistoryWorkspace";

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)); }); };
async function mount(fetcher: typeof fetch, strictMode = false) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  sessionStorage.clear();
  vi.stubGlobal("fetch", fetcher);
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = <QueryClientProvider client={client}><ResearchHistoryWorkspace projectId={ids.project} threadId={ids.thread} /></QueryClientProvider>;
  await act(async () => root!.render(strictMode ? <StrictMode>{view}</StrictMode> : view));
  await flush();
}
function button(text: string): HTMLButtonElement {
  const match = [...document.querySelectorAll("button")].find(item => item.textContent?.includes(text));
  if (!match) throw new Error(`button not found: ${text}`);
  return match;
}
async function click(text: string) { await act(async () => button(text).click()); await flush(); }
async function choose(title: string, side: "비교 전" | "비교 후") {
  const row = [...container.querySelectorAll(".history-events li")].find(item => item.textContent?.includes(title));
  const choice = [...(row?.querySelectorAll("button") ?? [])].find(item => item.textContent?.trim() === side);
  if (!choice) throw new Error(`comparison choice not found: ${title} ${side}`);
  await act(async () => choice.click()); await flush();
}
afterEach(async () => {
  if (root) await act(async () => root!.unmount()); root = undefined;
  client?.clear(); container?.remove(); sessionStorage.clear(); vi.unstubAllGlobals();
});

it("shows the exact first and second answers, with query-only navigation", async () => {
  const fixture = createHistoryFixtureTransport(); await mount(fixture.fetch);
  expect(container.textContent).toContain("최신 기록부터");
  await click("첫 질문에 대한 답변");
  expect(container.querySelector(".history-detail")?.textContent).toContain("첫 번째 답변:");
  expect(container.querySelector(".history-detail")?.textContent).not.toContain("두 번째 답변:");
  await click("현재와 비교");
  expect(container.querySelector(".history-comparison")?.textContent).toContain("첫 번째 답변:");
  expect(container.querySelector(".history-comparison")?.textContent).toContain("두 번째 답변:");
  await click("추가 질문에 대한 답변");
  expect(container.querySelector(".history-detail")?.textContent).toContain("두 번째 답변:");
  expect(fixture.calls.every(call => call.url === "/rpc/query")).toBe(true);
  expect(fixture.calls.some(call => /restore\/apply|thread\/input/.test(call.method))).toBe(false);
});

it("selects two exact RESULT identities and renders the real N2 query response", async () => {
  const fixture = createHistoryFixtureTransport(); await mount(fixture.fetch);
  await choose("첫 질문에 대한 답변", "비교 전");
  expect(fixture.calls.filter(call => call.method === "thread/result/compare/read")).toHaveLength(0);
  await choose("추가 질문에 대한 답변", "비교 후");
  const reads = fixture.calls.filter(call => call.method === "thread/result/compare/read");
  expect(reads).toHaveLength(1);
  expect(reads[0]).toMatchObject({ url: "/rpc/query", input: { project_id: ids.project, thread_id: ids.thread, contract_version: 2,
    before: { request_revision_digest: ids.request1, result_revision_digest: ids.result1 },
    after: { request_revision_digest: ids.request2, result_revision_digest: ids.result2 } } });
  expect(container.querySelector(".decision-delta-view")?.textContent).toContain("답변 내용");
  expect(container.querySelector(".decision-delta-view")?.textContent).toContain("첫 번째 답변:");
  expect(container.querySelector(".decision-delta-view")?.textContent).toContain("두 번째 답변:");
  expect(container.querySelector(".decision-delta-view")?.textContent).toContain("변경 이유가 기록되지 않았습니다");
});

it("does not duplicate the exact N2 query in the app's StrictMode", async () => {
  const fixture = createHistoryFixtureTransport(); await mount(fixture.fetch, true);
  await choose("첫 질문에 대한 답변", "비교 전");
  await choose("추가 질문에 대한 답변", "비교 후");
  expect(fixture.calls.filter(call => call.method === "thread/result/compare/read")).toHaveLength(1);
});

it("keeps selected before and after results while loading later history pages", async () => {
  const fixture = createHistoryFixtureTransport();
  const fetcher: typeof fetch = async (url, init) => {
    const response = await fixture.fetch(url, init);
    const request = JSON.parse(String(init?.body));
    if (request.method !== "revision/timeline/read") return response;
    const envelope = await response.json();
    const cursor = request.params.input.cursor;
    envelope.result.value.items = cursor === null ? envelope.result.value.items.filter((item: { item_id: string }) => item.item_id === "result-1")
      : cursor === "page-2" ? envelope.result.value.items.filter((item: { item_id: string }) => item.item_id === "result-2") : [];
    envelope.result.value.next_cursor = cursor === null ? "page-2" : cursor === "page-2" ? "page-3" : null;
    return new Response(JSON.stringify(envelope));
  };
  await mount(fetcher); await choose("첫 질문에 대한 답변", "비교 전");
  await click("다음 기록 더 보기");
  expect(container.textContent).toContain("비교 전: 첫 질문에 대한 답변");
  await choose("추가 질문에 대한 답변", "비교 후");
  expect(container.querySelector(".decision-delta-view")).not.toBeNull();
  await click("다음 기록 더 보기");
  expect(container.textContent).toContain("비교 전: 첫 질문에 대한 답변");
  expect(container.textContent).toContain("비교 후: 추가 질문에 대한 답변");
  expect(fixture.calls.filter(call => call.method === "thread/result/compare/read")).toHaveLength(1);
});

it("keeps the exact pair across a same-actor history refetch and rechecks N2", async () => {
  const fixture = createHistoryFixtureTransport(); await mount(fixture.fetch);
  await choose("첫 질문에 대한 답변", "비교 전"); await choose("추가 질문에 대한 답변", "비교 후");
  await act(async () => { await client.invalidateQueries({ queryKey: ["research-history", ids.project] }); }); await flush(); await flush();
  expect(container.textContent).toContain("비교 전: 첫 질문에 대한 답변");
  expect(container.textContent).toContain("비교 후: 추가 질문에 대한 답변");
  expect(container.querySelector(".decision-delta-view")?.textContent).toContain("첫 번째 답변:");
  expect(fixture.calls.filter(call => call.method === "thread/result/compare/read")).toHaveLength(2);
});

it("hides the old delta when the same actor's N2 access is revoked on refetch", async () => {
  const fixture = createHistoryFixtureTransport(); let revoked = false;
  const fetcher: typeof fetch = async (url, init) => {
    const request = JSON.parse(String(init?.body));
    if (revoked && request.method === "thread/result/compare/read") return new Response(JSON.stringify({ jsonrpc: "2.0", id: request.id,
      error: { code: -32003, message: "access denied", data: { reason_code: "HISTORY_SCOPE_UNAVAILABLE" } } }));
    return fixture.fetch(url, init);
  };
  await mount(fetcher); await choose("첫 질문에 대한 답변", "비교 전"); await choose("추가 질문에 대한 답변", "비교 후");
  expect(container.querySelector(".decision-delta-view")?.textContent).toContain("첫 번째 답변:");
  revoked = true;
  await act(async () => { await client.invalidateQueries({ queryKey: ["research-history", ids.project] }); }); await flush(); await flush();
  expect(container.textContent).toContain("비교 전: 첫 질문에 대한 답변");
  expect(container.querySelector(".decision-delta-view")).toBeNull();
  expect(container.textContent).toContain("비교를 읽지 못했습니다");
});

it("bounds a large actual-shape delta until each group and full value is explicitly opened", async () => {
  const fixture = createHistoryFixtureTransport();
  const longAnswer = "의미 있는 답변 ".repeat(180);
  const technicalValue = "technical-digest-" + "a".repeat(64);
  const change = (path: string, before: unknown, after: unknown) => ({ path, before_present: true, after_present: true, before, after });
  const content = [change("/result/answer", "기존 답변", longAnswer),
    change("/result/claim/0", { id: technicalValue, summary: "기존 핵심 주장" }, { id: technicalValue, summary: "새 핵심 주장" }),
    ...Array.from({ length: 10 }, (_, index) => change(`/result/claim/${index + 1}`, `기존 주장 ${index}`, `새 주장 ${index}`))];
  const evidence = Array.from({ length: 20 }, (_, index) => change(`/result/evidence/${index}`, `근거 ${index}`, `추가 근거 ${index}`));
  const other = Array.from({ length: 56 }, (_, index) => change(`/result/metadata/record_${index}_digest`, technicalValue, `${technicalValue}-${index}`));
  const fetcher: typeof fetch = async (url, init) => {
    const response = await fixture.fetch(url, init);
    const request = JSON.parse(String(init?.body));
    if (request.method !== "thread/result/compare/read") return response;
    const envelope = await response.json();
    envelope.result.value.groups = [
      { kind: "CONTENT", trace_paths: content.map(item => item.path), changes: content },
      { kind: "EVIDENCE", trace_paths: evidence.map(item => item.path), changes: evidence },
      { kind: "OTHER", trace_paths: other.map(item => item.path), changes: other },
    ];
    envelope.result.value.reason_state = "RECORDED";
    envelope.result.value.reason_codes = ["RECORDED_1", "RECORDED_2", "RECORDED_3", "RECORDED_4"];
    envelope.result.value.reason_refs = [{ entity_type: "EVIDENCE", entity_id: "evidence:recorded", revision_digest: "f".repeat(64) }];
    return new Response(JSON.stringify(envelope));
  };
  await mount(fetcher); await choose("첫 질문에 대한 답변", "비교 전"); await choose("추가 질문에 대한 답변", "비교 후");
  const panel = container.querySelector(".decision-delta-view")!;
  expect(panel.textContent).toContain("서버 기록 변경 88건 중 5건");
  expect(panel.querySelectorAll(".decision-delta-change")).toHaveLength(5);
  expect(panel.textContent!.length).toBeLessThan(6000);
  expect(panel.textContent).not.toContain(technicalValue);
  expect(panel.textContent).toContain("새 핵심 주장");
  expect(panel.textContent).not.toContain(longAnswer);
  expect(panel.textContent).not.toContain("evidence:recorded");
  const fullValue = [...panel.querySelectorAll("button")].find(item => item.textContent === "전체 값 보기")!;
  await act(async () => fullValue.click());
  expect(panel.textContent).toContain(longAnswer);
  const otherButton = [...panel.querySelectorAll("button")].find(item => item.textContent?.includes("상세 56건 보기"))!;
  await act(async () => otherButton.click());
  expect(panel.textContent).not.toContain(technicalValue);
  const otherGroup = [...panel.querySelectorAll(".decision-delta-group")].find(item => item.querySelector("h4")?.textContent?.includes("그 밖의 변경"))!;
  const technicalFullValue = [...otherGroup.querySelectorAll("button")].find(item => item.textContent === "전체 값 보기")!;
  await act(async () => technicalFullValue.click());
  expect(panel.textContent).toContain(technicalValue);
  expect(panel.querySelectorAll(".decision-delta-change").length).toBeLessThan(88);
  const reasonButton = [...panel.querySelectorAll("button")].find(item => item.textContent?.includes("이유 상세 보기"))!;
  await act(async () => reasonButton.click());
  expect(panel.textContent).toContain("evidence:recorded");
});

it("limits same-result, cross-thread and unavailable comparisons to the affected pair", async () => {
  const fixture = createHistoryFixtureTransport();
  const fetcher: typeof fetch = async (url, init) => {
    const response = await fixture.fetch(url, init);
    const request = JSON.parse(String(init?.body));
    if (request.method !== "revision/timeline/read") return response;
    const envelope = await response.json();
    envelope.result.value.items.find((item: { item_id: string }) => item.item_id === "result-2").scope_links[0].thread_id = "other-thread";
    return new Response(JSON.stringify(envelope));
  };
  await mount(fetcher);
  await choose("첫 질문에 대한 답변", "비교 전");
  await choose("첫 질문에 대한 답변", "비교 후");
  expect(container.textContent).toContain("서로 다른 답변 두 개");
  const second = [...container.querySelectorAll(".history-events li")].find(item => item.textContent?.includes("추가 질문에 대한 답변"));
  expect(second?.textContent).toContain("정확한 요청·결과 연결이 없어 비교할 수 없습니다");
  expect(fixture.calls.filter(call => call.method === "thread/result/compare/read")).toHaveLength(0);
});

it("shows NO_CHANGE and fails closed on a mismatched or denied N2 response", async () => {
  const fixture = createHistoryFixtureTransport(); let mode: "no-change" | "mismatch" | "denied" = "no-change";
  const fetcher: typeof fetch = async (url, init) => {
    const request = JSON.parse(String(init?.body));
    if (request.method !== "thread/result/compare/read") return fixture.fetch(url, init);
    if (mode === "denied") return new Response(JSON.stringify({ jsonrpc: "2.0", id: request.id,
      error: { code: -32003, message: "denied", data: { reason_code: "HISTORY_SCOPE_UNAVAILABLE" } } }));
    const envelope = await (await fixture.fetch(url, init)).json();
    if (mode === "no-change") { envelope.result.value.state = "NO_CHANGE"; envelope.result.value.groups = []; }
    if (mode === "mismatch") envelope.result.value.after.result_revision_digest = "f".repeat(64);
    return new Response(JSON.stringify(envelope));
  };
  await mount(fetcher); await choose("첫 질문에 대한 답변", "비교 전"); await choose("추가 질문에 대한 답변", "비교 후");
  expect(container.querySelector(".decision-delta-view")?.textContent).toContain("판단 변화가 없습니다");
  mode = "mismatch"; await click("비교 해제"); await choose("첫 질문에 대한 답변", "비교 전"); await choose("추가 질문에 대한 답변", "비교 후");
  expect(container.querySelector(".decision-delta-view")).toBeNull();
  expect(container.textContent).toContain("비교를 읽지 못했습니다");
  mode = "denied"; await click("다시 비교");
  expect(container.querySelector(".decision-delta-view")).toBeNull();
  expect(container.textContent).toContain("비교를 읽지 못했습니다");
});

it("keeps PARTIAL and UNAVAILABLE comparison states distinct", async () => {
  const fixture = createHistoryFixtureTransport(); let state: "PARTIAL" | "UNAVAILABLE" = "PARTIAL";
  const fetcher: typeof fetch = async (url, init) => {
    const response = await fixture.fetch(url, init);
    const request = JSON.parse(String(init?.body));
    if (request.method !== "thread/result/compare/read") return response;
    const envelope = await response.json(); envelope.result.value.state = state;
    return new Response(JSON.stringify(envelope));
  };
  await mount(fetcher); await choose("첫 질문에 대한 답변", "비교 전"); await choose("추가 질문에 대한 답변", "비교 후");
  expect(container.querySelector(".decision-delta-view")?.textContent).toContain("일부 변경만 확인됐습니다");
  state = "UNAVAILABLE"; await click("비교 해제");
  await choose("첫 질문에 대한 답변", "비교 전"); await choose("추가 질문에 대한 답변", "비교 후");
  expect(container.querySelector(".decision-delta-view")?.textContent).toContain("비교 내용을 읽을 수 없습니다");
  expect(container.querySelector(".decision-delta-view")?.textContent).not.toContain("첫 번째 답변:");
});

it("discards a late comparison after ordinary selection or actor scope changes", async () => {
  const fixture = createHistoryFixtureTransport(); let release!: () => void; let actorChanged = false;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  const fetcher: typeof fetch = async (url, init) => {
    const request = JSON.parse(String(init?.body));
    if (request.method === "thread/result/compare/read") await delayed;
    const response = await fixture.fetch(url, init);
    if (actorChanged && request.method === "revision/timeline/read") {
      const envelope = await response.json(); envelope.result.value.actor_scope_digest = "f".repeat(64);
      return new Response(JSON.stringify(envelope));
    }
    return response;
  };
  await mount(fetcher); await choose("첫 질문에 대한 답변", "비교 전"); await choose("추가 질문에 대한 답변", "비교 후");
  await click("초기 가설");
  release(); await flush();
  expect(container.querySelector(".decision-delta-view")).toBeNull();
  await choose("첫 질문에 대한 답변", "비교 전"); await choose("추가 질문에 대한 답변", "비교 후");
  expect(container.querySelector(".decision-delta-view")).not.toBeNull();
  actorChanged = true;
  await act(async () => { await client.invalidateQueries({ queryKey: ["research-history", ids.project] }); }); await flush();
  expect(container.querySelector(".decision-delta-view")).toBeNull();
  expect(container.textContent).toContain("비교 전: 선택 전");
});

it("discards a late detail response after a different record was selected", async () => {
  const fixture = createHistoryFixtureTransport();
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  const fetcher: typeof fetch = async (url, init) => {
    const request = JSON.parse(String(init?.body));
    if (request.method === "revision/timeline/item/read" && request.params.input.record_ref.revision_digest === ids.old) await delayed;
    return fixture.fetch(url, init);
  };
  await mount(fetcher);
  await click("초기 가설");
  await click("가설의 적용 조건");
  release(); await flush();
  expect(container.querySelector(".history-detail-heading")?.textContent).toContain("추가 근거에 맞춰");
  expect(container.querySelector(".history-detail-heading")?.textContent).not.toContain("초기 가설");
});

it("keeps apply disabled until the server readiness capability is true", async () => {
  const fixture = createHistoryFixtureTransport({ applyReady: false }); await mount(fixture.fetch);
  await click("초기 가설"); await click("복원 미리보기");
  expect(button("선택한 내용 적용").disabled).toBe(true);
  await click("선택한 내용 적용");
  expect(fixture.appliedCount).toBe(0);
  expect(fixture.calls.some(call => call.method === "revision/restore/apply")).toBe(false);
});

it("does not present a failed history request as an empty research history", async () => {
  await mount(async () => new Response(JSON.stringify({ jsonrpc: "2.0", id: "query", error: { code: -32601, message: "unsupported", data: {} } })));
  expect(container.textContent).toContain("기록의 유무를 확인하지 못했습니다");
  expect(container.textContent).not.toContain("아직 연구 기록이 없습니다");
});

it("restores keyboard focus to the preview opener when the dialog closes", async () => {
  await mount(createHistoryFixtureTransport().fetch);
  await click("초기 가설");
  const opener = button("복원 미리보기");
  await click("복원 미리보기"); await click("돌아가기");
  expect(document.activeElement).toBe(opener);
});

it("resets all pages after an invalid cursor instead of appending a new first page", async () => {
  const fixture = createHistoryFixtureTransport(); let firstReads = 0;
  const cursors: unknown[] = [];
  const fetcher: typeof fetch = async (url, init) => {
    const request = JSON.parse(String(init?.body));
    if (request.method !== "revision/timeline/read") return fixture.fetch(url, init);
    cursors.push(request.params.input.cursor);
    if (request.params.input.cursor) return new Response(JSON.stringify({ jsonrpc: "2.0", id: request.id,
      error: { code: -32000, message: "cursor expired", data: { reason_code: "HISTORY_CURSOR_INVALID" } } }));
    firstReads += 1;
    const response = await fixture.fetch(url, init);
    const envelope = await response.json();
    envelope.result.value.items = firstReads === 1 ? envelope.result.value.items.slice(0, 1) : envelope.result.value.items.slice(-1);
    envelope.result.value.next_cursor = firstReads === 1 ? "opaque-old-page" : null;
    return new Response(JSON.stringify(envelope));
  };
  await mount(fetcher); await click("다음 기록 더 보기");
  expect(container.textContent).toContain("첫 구간부터 다시 불러옵니다");
  expect(cursors).toEqual([null, "opaque-old-page"]);
  await click("다시 읽기");
  expect(cursors).toEqual([null, "opaque-old-page", null]);
  expect(container.textContent).toContain("첫 질문에 대한 답변");
  expect(container.textContent).not.toContain("추가 근거에 맞춰 가설의 적용 조건");
});

it("does not infer preview support from the entity type or apply capability", async () => {
  const fixture = createHistoryFixtureTransport({ applyReady: true });
  const fetcher: typeof fetch = async (url, init) => {
    const response = await fixture.fetch(url, init);
    const envelope = await response.json();
    const request = JSON.parse(String(init?.body));
    if (request.method === "revision/timeline/item/read") delete envelope.result.value.item.capability.preview_supported;
    return new Response(JSON.stringify(envelope));
  };
  await mount(fetcher); await click("초기 가설");
  expect([...container.querySelectorAll("button")].some(item => item.textContent === "복원 미리보기")).toBe(false);
  expect(container.textContent).toContain("이 기록은 열람용");
});

it("keeps intermediate checkpoints available without presenting them as final answers", async () => {
  const fixture = createHistoryFixtureTransport();
  const fetcher: typeof fetch = async (url, init) => {
    const response = await fixture.fetch(url, init);
    const envelope = await response.json();
    const request = JSON.parse(String(init?.body));
    if (request.method === "revision/timeline/read") envelope.result.value.items = [{ ...envelope.result.value.items[1], completion: "CHECKPOINT", title: "연구 진행 중간 저장" }];
    return new Response(JSON.stringify(envelope));
  };
  await mount(fetcher);
  expect(container.textContent).toContain("이 구간에는 중간 저장 기록이 있습니다");
  expect(container.textContent).not.toContain("아직 연구 기록이 없습니다");
  await act(async () => (container.querySelector('input[type="checkbox"]') as HTMLInputElement).click());
  expect(container.querySelector(".history-events")?.textContent).toContain("연구 진행 중간 저장");
  expect(container.querySelector(".history-event-meta")?.textContent).toContain("중간 저장");
});

it("hides cached record content while a changed actor scope is being checked", async () => {
  const fixture = createHistoryFixtureTransport(); let changed = false;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const fetcher: typeof fetch = async (url, init) => {
    const request = JSON.parse(String(init?.body));
    if (changed && request.method === "revision/timeline/item/read") {
      await pending;
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: request.id, error: { code: -32003, message: "access denied", data: { reason_code: "RESOURCE_SCOPE_DENIED" } } }));
    }
    const envelope = await (await fixture.fetch(url, init)).json();
    if (changed && request.method === "revision/timeline/read") envelope.result.value.actor_scope_digest = "f".repeat(64);
    return new Response(JSON.stringify(envelope));
  };
  await mount(fetcher); await click("초기 가설");
  expect(container.querySelector(".history-detail")?.textContent).toContain("관측 조건의 차이가");
  changed = true;
  await act(async () => { await client.invalidateQueries({ queryKey: ["research-history", ids.project] }); });
  await flush();
  try { expect(container.querySelector(".history-detail")?.textContent).not.toContain("관측 조건의 차이가"); }
  finally { release(); await flush(); }
});

it.each(["RESTORE_PREVIEW_STALE", "RESTORE_NOT_READY"])("requires a fresh preview after %s without creating another attempt", async reason => {
  const fixture = createHistoryFixtureTransport({ applyReady: true });
  const attempts: string[] = [];
  const fetcher: typeof fetch = async (url, init) => {
    const request = JSON.parse(String(init?.body));
    if (request.method === "revision/restore/apply") {
      attempts.push(request.params._meta.idempotencyKey);
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: request.id, error: { code: -32000, message: "preview refused", data: { reason_code: reason } } }));
    }
    return fixture.fetch(url, init);
  };
  await mount(fetcher); await click("초기 가설"); await click("복원 미리보기"); await click("선택한 내용 적용");
  expect(button("선택한 내용 적용").disabled).toBe(true);
  expect(attempts).toHaveLength(1);
  expect(fixture.appliedCount).toBe(0);
});

it("reopens a lost response using its original key and verifies the new head", async () => {
  const fixture = createHistoryFixtureTransport({ applyReady: true, loseApplyResponse: true }); await mount(fixture.fetch);
  await click("초기 가설"); await click("복원 미리보기"); await click("선택한 내용 적용");
  expect(document.body.textContent).toContain("원 요청의 결과를 확인해야");
  await click("돌아가기"); await click("복원 미리보기"); await click("원 요청 결과 확인");
  expect(document.body.textContent).toContain("내용을 복원했습니다. 관련 항목은 재검토가 필요합니다.");
  const applies = fixture.calls.filter(call => call.method === "revision/restore/apply");
  expect(applies).toHaveLength(2);
  expect(applies[0].key).toBe(applies[1].key);
  expect(applies[0].input).toEqual(applies[1].input);
  expect(fixture.appliedCount).toBe(1);
});
