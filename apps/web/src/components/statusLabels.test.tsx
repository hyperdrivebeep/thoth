import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { StatusBadge } from "./StatusBadge";
import { actionStateLabel, answerStatusLabel, counterReviewLabel, eyebrowLabel, providerLabel, reversibilityLabel, statusLabel } from "./statusLabels";

it("translates recorded internal values into plain Korean", () => {
  expect(statusLabel("OFFICIAL")).toBe("공식");
  expect(statusLabel("ELIGIBLE")).toBe("기준시점에 적합");
  expect(statusLabel("EXTRACTED")).toBe("원문 추출됨");
  expect(statusLabel("HUMAN_REQUIRED_R3")).toBe("사람 승인 필요");
  expect(statusLabel("AUTO_R0")).toBe("자동 실행 가능");
  expect(statusLabel("PREAUTHORIZED_R1")).toBe("사전 허용 범위");
  expect(statusLabel("SANDBOX_ONLY_R2")).toBe("격리 실행만");
  expect(statusLabel("PROHIBITED_R4")).toBe("실행 금지");
  expect(statusLabel("FULL")).toBe("되돌릴 수 있음");
});

it("covers every counter-evidence terminal and leaves unknown values alone", () => {
  for (const value of ["SUPPORTED", "ELIMINATED_WITHIN_SCOPE", "UNRESOLVED_NO_RESULTS", "UNRESOLVED_INDEPENDENCE", "UNRESOLVED_AUTHORITY",
    "UNRESOLVED_TEMPORAL", "UNRESOLVED_PROHIBITED_CONTEXT", "UNRESOLVED_POLICY_BLOCKED", "UNRESOLVED_FAILED", "UNRESOLVED_CONFLICT"]) {
    expect(counterReviewLabel(value)).toMatch(/[가-힣]/);
  }
  expect(statusLabel("SOMETHING_NEW")).toBeNull();
  expect(counterReviewLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
});

it("names answer status, provider routes and English eyebrows", () => {
  expect(answerStatusLabel("PARTIAL_HOLD")).toBe("판단 보류");
  expect(answerStatusLabel("ASSESSED_WITH_OPEN_CHECKS")).toBe("부분 답변");
  expect(answerStatusLabel("ASSESSED_FOR_REQUEST")).toBe("답변 완료");
  expect(answerStatusLabel(undefined)).toBeNull();
  expect(providerLabel("codex-oauth")).toBe("ChatGPT 계정");
  expect(providerLabel("mystery-route")).toBe("mystery-route");
  expect(eyebrowLabel("EVIDENCE")).toBe("근거");
  expect(eyebrowLabel("SOMETHING ELSE")).toBe("SOMETHING ELSE");
  expect(reversibilityLabel("PARTIAL")).toBe("일부만 되돌릴 수 있음");
  expect(actionStateLabel("APPROVED")).toBe("승인됨");
  expect(statusLabel("APPROVED")).toBe("승인본");
});

it("StatusBadge prefers the dictionary and falls back to the recorded value", () => {
  expect(renderToStaticMarkup(<StatusBadge value="OFFICIAL" />)).toContain("공식");
  expect(renderToStaticMarkup(<StatusBadge value="BRAND_NEW_STATE" />)).toContain("BRAND NEW STATE");
  expect(renderToStaticMarkup(<StatusBadge value={null} />)).toContain("미기록");
});
