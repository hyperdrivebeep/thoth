// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { rpc } from "../api/rpcClient";
import { ConversationTimeline } from "./ConversationTimeline";

vi.mock("../api/rpcClient", async importOriginal => ({
  ...await importOriginal<typeof import("../api/rpcClient")>(),
  rpc: vi.fn(),
}));

let root: Root | undefined;
let container: HTMLDivElement;
let client: QueryClient;

async function settle(check: () => boolean) {
  for (let index = 0; index < 50; index += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
    if (check()) return;
  }
  throw new Error("Expected legacy empty state was not rendered");
}

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  client?.clear();
  container?.remove();
  vi.resetAllMocks();
});

it("does not present an existing legacy thread as a brand-new empty thread", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(rpc).mockResolvedValue({
    operation_id: "",
    state: "SUCCEEDED",
    value: {
      conversation: { turns: [], next_before_epoch: null, history_limited: false },
    },
  });
  const onContinue = vi.fn();
  const onOpenHistory = vi.fn();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root!.render(
    <QueryClientProvider client={client}>
      <ConversationTimeline projectId="p" threadId="legacy-thread" onDetail={() => undefined} onContinue={onContinue} onOpenHistory={onOpenHistory}/>
    </QueryClientProvider>,
  ));
  await settle(() => Boolean(container.textContent?.includes("이 작업의 대화 기록을 현재 화면에서 불러올 수 없습니다")));
  expect(container.textContent).not.toContain("지금 무엇이 막혀 있나요?");

  const buttons = [...container.querySelectorAll<HTMLButtonElement>("button")];
  await act(async () => buttons.find(button => button.textContent === "새 질문으로 이어가기")!.click());
  await act(async () => buttons.find(button => button.textContent === "연구 이력 확인")!.click());
  expect(onContinue).toHaveBeenCalledOnce();
  expect(onOpenHistory).toHaveBeenCalledOnce();
});
