// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { ResearchStatus } from "../api/research";
import { rpc } from "../api/rpcClient";
import { ConversationTimeline } from "./ConversationTimeline";

// The answer checks are sent for the latest request only. A saved round before it will never get them, so its
// card says so; "not available yet" is kept for a latest round whose checks have not arrived.
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
const result = (epoch: number) => ({
  thread_id: "t",
  request_epoch: epoch,
  contract_version: 2,
  answer: epoch + "번째 답",
  answer_status: "PARTIAL_HOLD",
});
// The live status describes the second request and carries no checks for it yet.
const status = {
  user_progress_summary: {
    schema_version: "1.0.0",
    request_revision_digest: digest("b"),
    result_revision_digest: digest("c"),
    state: "HOLD",
    currentness: { state: "CURRENT", reasons: [], execution_eligible: true },
    progress_items: [],
    recorded_checks: [],
    remaining_gaps: [],
    unknowns: [],
    next_user_action: {
      schema_version: "1.0.0",
      action_type: "NONE",
      label: "",
      reason_codes: [],
      requires_permission: false,
      target: null,
      basis: {},
    },
  },
  coverage_matrix: null,
  next_user_action: null,
} as unknown as ResearchStatus;
const EARLIER = "이 회차의 답변 점검 항목은 여기서 볼 수 없습니다. 최신 회차의 답에서만 보입니다.";
const NOT_YET = "답변 점검 항목 정보를 아직 읽지 못했습니다";
const NO_USAGE = "이 결과의 사용량 기록 없음";

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  client?.clear();
  container?.remove();
  vi.resetAllMocks();
});

type Stored = { state: string; result: unknown; error?: unknown };
async function mount(
  liveStatus: ResearchStatus | undefined,
  stored: (epoch: number) => Stored = (epoch) => ({ state: "SUCCEEDED", result: result(epoch) }),
) {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.mocked(rpc).mockImplementation(async (method, args) => {
    if (method === "thread/activity/list")
      return {
        operation_id: "",
        state: "SUCCEEDED",
        value: { conversation: { turns, next_before_epoch: null, history_limited: false } },
      };
    if (method === "operation/result/read") {
      const epoch = Number(String(args.operation_id).split(":")[1]);
      return {
        operation_id: "",
        state: "SUCCEEDED",
        value: { operation_id: args.operation_id, ...stored(epoch) },
      };
    }
    throw new Error("not served: " + method);
  });
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () =>
    root!.render(
      <QueryClientProvider client={client}>
        <ConversationTimeline
          projectId="p"
          threadId="t"
          status={liveStatus}
          onDetail={() => undefined}
        />
      </QueryClientProvider>,
    ),
  );
  for (let index = 0; index < 40 && !container.textContent?.includes("2번째 답"); index += 1)
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  // the first round is folded to one line; open it
  const open = container.querySelector<HTMLButtonElement>(".turn-summary-actions button");
  expect(open).not.toBeNull();
  await act(async () => open!.click());
}
const cards = () =>
  [...container.querySelectorAll(".conversation-turn")].map((element) => element.textContent ?? "");

it("tells an earlier round that its answer checks are not shown here, and the latest round that they have not arrived", async () => {
  await mount(status);
  const [first, latest] = cards();
  expect(first).toContain("1번째 답");
  expect(first).toContain(EARLIER);
  expect(first).not.toContain(NOT_YET);
  expect(latest).toContain(NOT_YET);
  expect(latest).not.toContain(EARLIER);
  // With no usage sent for either round, each card says that none was recorded.
  expect(first).toContain(NO_USAGE);
  expect(latest).toContain(NO_USAGE);
});

it("shows the usage the server recorded for an earlier round, and says none only for a round without any", async () => {
  const usage = (operation: string, tokens: number) => ({
    operation_id: operation,
    calls: 2,
    input_tokens: tokens - 10,
    output_tokens: 10,
    total_tokens: tokens,
    cached_input_tokens: null,
    unreported_calls: 0,
    state: "OBSERVED",
    wall_ms: 120_000,
  });
  await mount({
    ...status,
    result_usage: { "operation:1": usage("operation:1", 4321) },
  } as ResearchStatus);
  const [first, latest] = cards();
  expect(first).toContain("4,321");
  expect(first).not.toContain(NO_USAGE);
  expect(latest).toContain(NO_USAGE);
  expect(latest).not.toContain("4,321");
});

it("does not call a round earlier when nothing says which request is the latest", async () => {
  await mount(undefined);
  for (const card of cards()) {
    expect(card).toContain(NOT_YET);
    expect(card).toContain(NO_USAGE);
    expect(card).not.toContain(EARLIER);
  }
});

const STOPPED = "직접 중지했습니다.";
const CANCEL_UNKNOWN = "취소 사유는 현재 기록에서 확인되지 않습니다.";

it("tells the turn of a round the person stopped that they stopped it, from operation/result/read alone", async () => {
  await mount(undefined, (epoch) =>
    epoch === 1
      ? {
          state: "CANCELLED",
          result: null,
          error: { message: "USER_STOPPED", data: { reason_code: "USER_STOPPED" } },
        }
      : { state: "SUCCEEDED", result: result(epoch) },
  );
  const [first, latest] = cards();
  expect(first).toContain(STOPPED);
  expect(first).not.toContain(CANCEL_UNKNOWN);
  expect(latest).not.toContain(STOPPED);
});

it("keeps the old sentence for a cancelled turn that has no reason on record", async () => {
  await mount(undefined, (epoch) =>
    epoch === 1
      ? { state: "CANCELLED", result: null, error: null }
      : { state: "SUCCEEDED", result: result(epoch) },
  );
  const [first] = cards();
  expect(first).toContain(CANCEL_UNKNOWN);
  expect(first).not.toContain(STOPPED);
});
