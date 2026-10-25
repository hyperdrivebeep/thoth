// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import demo from "./traceDemo.fixture.json";
import type { TraceView } from "../api/trace";
import { investigationFor } from "./traceInvestigation";
import { setLocale } from "../i18n/locale";

afterEach(() => setLocale("ko"));
it("uses the outgoing response language independently of the display language", () => {
  const view = demo.phase2 as unknown as TraceView;
  const verdict = view.verdicts.find(item => item.subject_kind === "CRITERION" && item.state === "FAIL_COMPUTED")!;
  const title = view.items.find(item => item.item_id === verdict.subject_id)!.title;
  setLocale("ko");
  const english = investigationFor("p", "CRITERION", verdict, view, "en")!;
  expect(english.question).toContain("Find possible causes and tests that distinguish them.");
  expect(english.question).toContain(title);
  expect(english.question).not.toMatch(/[가-힣]/);
  expect(english.question).not.toMatch(/FAIL_COMPUTED|THRESHOLD_NOT_MET/);
  setLocale("en");
  const korean = investigationFor("p", "CRITERION", verdict, view, "ko")!;
  expect(korean.question).toContain("원인 후보와 그것을 가를 시험을 찾아 주세요.");
  expect(english.origin).toEqual(korean.origin);
  expect(english.questionForLanguage?.("ko")).toBe(korean.question);
  expect(english.questionForLanguage?.(null)).toBe(english.question);
  setLocale("ko");
  expect(english.questionForLanguage?.(null)).toBe(korean.question);
});

it.each([
  ["en", undefined], ["en", null], ["ko", undefined], ["ko", null],
] as const)("uses display %s when the response choice is %s", (display, response) => {
  setLocale(display);
  const view = demo.phase2 as unknown as TraceView;
  const verdict = view.verdicts.find(item => item.subject_id === "SYN-C-DET-RAIN")!;
  const generated = investigationFor("p", "CRITERION", verdict, view, response)!;
  if (display === "en") {
    expect(generated.question).toContain("Find possible causes and tests that distinguish them.");
    expect(generated.question).not.toMatch(/[가-힣]/);
  } else {
    expect(generated.question).toContain("원인 후보와 그것을 가를 시험을 찾아 주세요.");
    expect(generated.question).toContain("측정값 0.80이 기준 0.90 이상(ratio)에 못 미칩니다.");
  }
  expect(generated.question).toContain("Detection rate in rain");
  expect(generated.responseLanguage).toBe(response);
  expect(generated.origin.verdict_revision).toBe(verdict.revision_digest);
});
