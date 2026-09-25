import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { withCurrentCheckpoint, type ConversationTurn } from "../api/conversation";
import { ResearchResultCard } from "./ResearchResultCard";

const input = { request_epoch: 1, request_revision_digest: "request-1", operation_id: "op-1", text: "question", edit_kind: "APPEND", created_at: "2026-09-21T00:00:00Z", authored_text_ref: { revision_digest: "authored-1" } };
const checkpoint = { operation_id: "op-1", request_ref: { revision_digest: "request-1" }, result: { requirements: {} }, completion: "CHECKPOINT" as const };

it("keeps CANCELLED on its own previous checkpoint and renders no invented saved answer", () => {
  const turn: ConversationTurn = { input, result: null, state: "CANCELLED" };
  const merged = withCurrentCheckpoint([turn], { request: { operation_id: "op-1" }, operation_state: "CANCELLED", previous_result: checkpoint, basis_currentness: { state: "REVIEW_REQUIRED", reasons: ["ATTEMPT_ENDED_WITH_CHECKPOINT"] } })[0];
  expect(merged.state).toBe("CANCELLED");
  const html = renderToStaticMarkup(<ResearchResultCard {...merged} onDetail={() => undefined} />);
  expect(html).toContain("연구 실행이 취소되었습니다");
  expect(html).toContain("저장된 답변은 없습니다");
  expect(html).not.toContain("당시 저장된 답변");
  expect(html).not.toContain("사용자가 취소");
});

it("does not attribute a later cancellation to a completed earlier answer", () => {
  const turn: ConversationTurn = { input, result: { answer: "earlier answer" }, state: "SUCCEEDED" };
  const merged = withCurrentCheckpoint([turn], { request: { operation_id: "op-2" }, operation_state: "CANCELLED", previous_result: { ...checkpoint, result: turn.result! } })[0];
  expect(merged.state).toBe("STALE");
  expect(merged.result?.answer).toBe("earlier answer");
});

it("shows cancellation independently from content and currentness", () => {
  const html = renderToStaticMarkup(<ResearchResultCard state="CANCELLED" result={{ requirements: {} }} currentness={{ state: "REVIEW_REQUIRED", reasons: ["ATTEMPT_ENDED_WITH_CHECKPOINT"] }} onDetail={() => undefined} />);
  expect(html).toContain("연구 실행이 취소되었습니다");
  expect(html).toContain("저장된 답변은 없습니다");
  expect(html).not.toContain("당시 저장된 답변");
  expect(html).not.toContain("과거 답변");
  expect(html).not.toContain("원격 모델의 종료 여부는 확인되지");
});

it("preserves partial content on cancellation without calling it a processing failure", () => {
  const html = renderToStaticMarkup(<ResearchResultCard state="CANCELLED" result={{ answer: "partial observation" }} onDetail={() => undefined} />);
  expect(html).toContain("연구 실행이 취소되었습니다");
  expect(html).toContain("partial observation");
  expect(html).not.toContain("내부 연구 처리에 실패");
  expect(html).not.toContain("저장된 답변은 없습니다");
});
