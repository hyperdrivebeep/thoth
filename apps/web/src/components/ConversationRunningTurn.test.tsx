// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { ResearchStatus } from "../api/research";
import { rpc } from "../api/rpcClient";
import { ConversationTimeline } from "./ConversationTimeline";

// While a question is being researched its turn is already in the saved list, with no result yet. It used to be
// drawn as a folded line saying "no answer" with an "ask again" button, above the live progress of that very
// question (seen with a real model; a fake model finishes before the screen can show it).
vi.mock("../api/rpcClient", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/rpcClient")>()),
  rpc: vi.fn(),
}));

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;
const digest = (letter: string) => letter.repeat(64);
const turn = (epoch: number, letter: string, text: string) => ({
  request_epoch: epoch,
  request_revision_digest: digest(letter),
  operation_id: "operation:" + epoch,
  text,
  edit_kind: "NEW",
  created_at: "2026-11-12T00:0" + epoch + ":00Z",
  authored_text_ref: { revision_digest: digest(letter) },
});
const turns = [turn(1, "a", "첫 질문"), turn(2, "b", "둘째 질문")];
const status = (state: string, operation: string) =>
  ({
    operation_state: state,
    request: { operation_id: operation, authored_text: "둘째 질문" },
  }) as unknown as ResearchStatus;

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  client?.clear();
  container?.remove();
  vi.resetAllMocks();
});

async function mount(liveStatus: ResearchStatus | undefined, secondState = "RUNNING") {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(rpc).mockImplementation(async (method, args) => {
    if (method === "thread/activity/list")
      return {
        operation_id: "",
        state: "SUCCEEDED",
        value: { conversation: { turns, next_before_epoch: null, history_limited: false } },
      };
    if (method === "operation/result/read") {
      const first = args.operation_id === "operation:1";
      return {
        operation_id: "",
        state: "SUCCEEDED",
        value: {
          operation_id: args.operation_id,
          state: first ? "SUCCEEDED" : secondState,
          result: first
            ? {
                thread_id: "t",
                request_epoch: 1,
                contract_version: 2,
                answer: "첫 답",
                answer_status: "ANSWERED",
              }
            : null,
        },
      };
    }
    throw new Error("not served: " + method);
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await show(liveStatus);
  for (
    let index = 0;
    index < 40 && container.querySelectorAll(".conversation-turn").length < 2;
    index += 1
  )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
}
const show = (liveStatus: ResearchStatus | undefined) =>
  act(async () =>
    root!.render(
      <QueryClientProvider client={client}>
        <ConversationTimeline
          projectId="p"
          threadId="t"
          status={liveStatus}
          onDetail={() => undefined}
          onRetry={() => undefined}
        />
      </QueryClientProvider>,
    ),
  );
const second = () => container.querySelector('.conversation-turn[data-turn-index="2"]')!;

it("draws the turn being researched as its question only, with no 'no answer' line and no ask-again button", async () => {
  for (const state of ["RUNNING", "PENDING"]) {
    await mount(status(state, "operation:2"));
    expect(second().querySelector(".user-message")?.textContent).toContain("둘째 질문");
    // nothing but the question: no folded line, and no empty result card either (a research about to start has none)
    expect(second().hasAttribute("data-turn-running"), state).toBe(true);
    expect(second().querySelector(".assistant-message"), state).toBeNull();
    expect(second().querySelector(".turn-summary")).toBeNull();
    expect(second().textContent).not.toContain("답 없음");
    expect(
      [...container.querySelectorAll("button")].map((button) => button.textContent),
    ).not.toContain("같은 질문으로 다시 조사");
    // the earlier turn keeps its folded line
    expect(
      container.querySelector('.conversation-turn[data-turn-index="1"] .turn-summary')?.textContent,
    ).toContain("1회차");
    await act(async () => root!.unmount());
    root = undefined;
    client.clear();
    container.remove();
  }
});

it("opens the answer when its research ends: the turn that was only a question becomes the open result card", async () => {
  await mount(status("RUNNING", "operation:2"));
  expect(second().querySelector(".assistant-message")).toBeNull();
  // the same screen, a moment later: the status now carries the result of that request
  const finished = {
    operation_state: "SUCCEEDED",
    request: { operation_id: "operation:2", authored_text: "둘째 질문" },
    current_result: {
      operation_id: "operation:2",
      request_ref: { revision_digest: digest("b") },
      terminal_reason: null,
      result: {
        thread_id: "t",
        request_epoch: 2,
        contract_version: 2,
        answer: "둘째 답입니다",
        answer_status: "ANSWERED",
      },
    },
  } as unknown as ResearchStatus;
  await show(finished);
  expect(second().hasAttribute("data-turn-running")).toBe(false);
  expect(second().querySelector(".assistant-message .answer-text")?.textContent).toContain(
    "둘째 답입니다",
  );
  // not behind a folded line with a preview and an "expand" button
  expect(second().querySelector(".turn-summary")).toBeNull();
  // the earlier turn was folded and stays folded; nothing folds or opens on its own besides the new answer
  expect(
    container.querySelector('.conversation-turn[data-turn-index="1"] .assistant-message'),
  ).toBeNull();
});

it("keeps the line and the ask-again button for a turn that ended without an answer while another question runs", async () => {
  await mount(status("RUNNING", "operation:9"), "FAILED");
  expect(second().querySelector(".turn-summary")).not.toBeNull();
  expect([...second().querySelectorAll("button")].map((button) => button.textContent)).toContain(
    "같은 질문으로 다시 조사",
  );
});

it("draws a turn whose research is over as its result card, whatever the old live status said", async () => {
  for (const live of [status("FAILED", "operation:2"), undefined]) {
    await mount(live, "FAILED");
    expect(second().hasAttribute("data-turn-running")).toBe(false);
    expect(second().querySelector(".assistant-message")).not.toBeNull();
    await act(async () => root!.unmount());
    root = undefined;
    client.clear();
    container.remove();
  }
});
