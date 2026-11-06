// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { ConversationTimeline } from "./ConversationTimeline";
import { ConversationTurnView } from "./ConversationTurnView";
import { turnSummary } from "./turnSummary";
import { turnTally } from "./timelineTurns";
import { rpc } from "../api/rpcClient";
import type { ConversationTurn } from "../api/conversation";
import type { ResearchStatus } from "../api/research";
import { setLocale } from "../i18n/locale";

vi.mock("../api/rpcClient", async importOriginal => ({ ...await importOriginal<typeof import("../api/rpcClient")>(), rpc: vi.fn() }));

const input = (n: number) => ({ request_epoch: n, request_revision_digest: `request-${n}`, operation_id: `op-${n}`,
  text: `question-${n}`, edit_kind: "APPEND", created_at: "2026-11-05T09:45:09Z", authored_text_ref: { revision_digest: `authored-${n}` } });
const turn = (n: number, extra: Partial<ConversationTurn> = {}): ConversationTurn => ({ input: input(n), result: { thread_id: "t", request_epoch: n, answer: `검증 보류 · 부분 초안: ## 사실\n저장된 답변 ${n} (span:aaaa1111)`, answer_status: "PARTIAL_HOLD" }, state: "SUCCEEDED", ...extra });
const cutOff = (n: number): ConversationTurn => ({ input: input(n), result: null, state: "SUCCEEDED", terminalReason: "OAUTH_TRANSPORT_FAILURE", failure: { primary: { origin: "MODEL_CALL", reason_code: "OAUTH_TRANSPORT_FAILURE" }, remote_observation: "UNKNOWN" } });

afterEach(() => { setLocale("ko"); vi.resetAllMocks(); });

it("summarises a folded turn from the same facts as the result card: hold status, cut-off reason, currentness", () => {
  const held = turnSummary(turn(1));
  expect(held.tags.map(tag => tag.text)).toEqual(["판단 보류"]);
  expect(held.preview).toBe("저장된 답변 1"); // lead, heading and span id removed
  const cut = turnSummary(cutOff(2));
  expect(cut.tags.map(tag => tag.text)).toEqual(["끊김 · 연결 오류"]);
  expect(cut.hasAnswer).toBe(false);
  const stale = turnSummary(turn(3, { currentness: { state: "REVIEW_REQUIRED", reasons: ["MEMORY_CORRECTED_AFTER_RESULT"] } }));
  expect(stale.tags.map(tag => tag.text)).toEqual(["판단 보류", "재검토 필요"]);
  expect(stale.note).toContain("정정");
  expect(turnSummary({ input: input(4), result: null, state: "UNAVAILABLE", unavailable: "권한 없음" }).tags[0].text).toBe("볼 수 없음");
  expect(turnTally([turn(1), cutOff(2), turn(3, { state: "FAILED" })])).toEqual({ total: 3, answered: 2, interrupted: 2 });
});

it("renders the folded line with text tags only, keeps the ask-again button for a cut-off turn, and opens in place", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const retry = vi.fn();
  const node = document.createElement("div"); document.body.append(node);
  const root: Root = createRoot(node);
  await act(async () => root.render(<ConversationTurnView turn={cutOff(2)} index={2} expanded={false} onRetry={retry}><p className="full-card">전체 카드</p></ConversationTurnView>));
  expect(node.querySelector(".full-card")).toBeNull();
  expect(node.querySelector(".turn-summary")!.textContent).toContain("2회차");
  expect(node.querySelector(".turn-summary")!.textContent).toContain("끊김 · 연결 오류");
  expect(node.querySelector(".dot, [class*='dot']")).toBeNull();
  const buttons = [...node.querySelectorAll<HTMLButtonElement>("button")];
  await act(async () => buttons.find(button => button.textContent?.includes("같은 질문으로 다시 조사"))!.click());
  expect(retry).toHaveBeenCalledOnce();
  await act(async () => buttons.find(button => button.textContent?.includes("펼치기"))!.click());
  expect(node.querySelector(".full-card")!.textContent).toBe("전체 카드");
  expect(node.querySelector("[data-turn-open='true']")).not.toBeNull();
  await act(async () => [...node.querySelectorAll<HTMLButtonElement>("button")].find(button => button.textContent?.includes("접기"))!.click());
  expect(node.querySelector(".full-card")).toBeNull();
  await act(async () => root.unmount()); node.remove();
});

it("keeps only the newest saved turn open, folds the earlier ones, and counts them in the heading line", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(rpc).mockImplementation(async (method, args) => {
    if (method === "thread/activity/list") return { operation_id: "read", state: "SUCCEEDED", value: { conversation: { turns: [input(1), input(2), input(3)], next_before_epoch: null, history_limited: false } } };
    if (method === "operation/result/read") {
      const n = Number(String(args.operation_id).replace("op-", ""));
      if (n === 2) return { operation_id: "read", state: "SUCCEEDED", value: { operation_id: "op-2", state: "SUCCEEDED", result: { thread_id: "t", request_epoch: 2, terminal_reason: "OAUTH_TRANSPORT_FAILURE" } } };
      return { operation_id: "read", state: "SUCCEEDED", value: { operation_id: `op-${n}`, state: "SUCCEEDED", result: { thread_id: "t", request_epoch: n, answer: `저장된 답변 ${n}` } } };
    }
    return { operation_id: "read", state: "SUCCEEDED", value: {} };
  });
  const status = { project_id: "p", thread_id: "t", cycle_id: "c", problem: "q", lifecycle: "OPEN", execution_state: "IDLE", current_object_ids: [], working_head_digest: "h", operation_state: "SUCCEEDED", request: { operation_id: "op-3", authored_text: "question-3" } } as ResearchStatus;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const node = document.createElement("div"); document.body.append(node);
  const root = createRoot(node);
  await act(async () => root.render(<QueryClientProvider client={client}><ConversationTimeline projectId="p" threadId="t" status={status} onDetail={() => undefined}/></QueryClientProvider>));
  for (let i = 0; i < 50 && !node.querySelector("[data-turn-index='3']"); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  expect(node.querySelector("[data-turn-index='1']")!.getAttribute("data-turn-open")).toBe("false");
  expect(node.querySelector("[data-turn-index='2']")!.getAttribute("data-turn-open")).toBe("false");
  expect(node.querySelector("[data-turn-index='3']")!.getAttribute("data-turn-open")).toBe("true");
  expect(node.querySelectorAll(".assistant-message")).toHaveLength(1);
  expect(node.textContent).toContain("회차 3 · 답변 2 · 끊김·실패 1");
  expect(node.textContent).toContain("저장된 답변 1"); // the folded preview keeps the first line of the saved answer in view
  expect(node.textContent).toContain("저장된 답변 3");
  await act(async () => root.unmount()); node.remove(); client.clear();
});

it("words the folded tags in every screen language", () => {
  for (const [locale, expected] of [["en", "Cut off · Connection error"], ["zh-Hans", "中断"], ["ja", "中断"]] as const) {
    setLocale(locale);
    const html = renderToStaticMarkup(<ConversationTurnView turn={cutOff(2)} index={2} expanded={false}><p/></ConversationTurnView>);
    expect(html).toContain(expected);
  }
});
