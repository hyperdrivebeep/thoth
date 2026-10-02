import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { ResearchResultCard } from "./ResearchResultCard";

const held = { ...excerpt, answer_status: "PARTIAL_HOLD" } as Record<string, unknown>;
const spanA = (excerpt.answer.match(/span:[0-9A-Za-z][0-9A-Za-z_-]*/) ?? [""])[0];
const render = (result: Record<string, unknown>) => renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={result} onDetail={() => {}}/>);
const withFindings = (findings: unknown, extra: Record<string, unknown> = {}) => ({ ...held, confirmed_findings: findings, ...extra });
const fact = (statement: string, refs: string[], kind = "DOCUMENT_FACT") => ({ statement, evidence_refs: refs, requirement_id: null, kind });

it("says the result predates confirmed facts instead of pulling facts out of the answer text", () => {
  expect(held.answer).toContain("FACT");
  const html = render(held);
  expect(html).toContain("확인된 사실");
  expect(html).toContain("이 결과에는 정리된 확인 사실이 없습니다(이전 형식)");
});

it("lists the stored facts with their source numbers and marks a valid negative finding", () => {
  expect(spanA).not.toBe("");
  const html = render(withFindings([
    fact("RFP p.36에 1세부 과제명이 적혀 있다", [spanA]),
    fact("1세부 발췌에서 센서 조합의 필수 지정을 찾아봤지만 없다", [spanA, "span:not-cited-in-answer"], "VALID_NEGATIVE_FINDING"),
  ]));
  expect(html).toContain("RFP p.36에 1세부 과제명이 적혀 있다");
  expect(html).toContain("찾아봤지만 없음");
  expect(html).toMatch(/RFP p\.36에 1세부 과제명이 적혀 있다[\s\S]{0,300}\[1\]/);
  expect(html).not.toContain("이전 형식");
  // a source the answer never cited still gets its own number after the answer's
  expect(html).toMatch(/aria-label="근거 \d+ 보기">\[\d+\]<\/button><\/li>/);
});

it("keeps facts visible on a held answer, directly under the conclusion status", () => {
  const html = render(withFindings([fact("문서에 적힌 사실", [spanA])]));
  const at = (text: string) => html.lastIndexOf(text);
  expect(html.indexOf("결론 상태: <strong>판단 보류</strong>")).toBeGreaterThan(-1);
  expect(html.indexOf("결론 상태: <strong>판단 보류</strong>")).toBeLessThan(html.indexOf("확인된 사실"));
  expect(html.indexOf("확인된 사실")).toBeLessThan(html.indexOf("문서에 적힌 사실"));
  expect(at("문서에 적힌 사실")).toBeGreaterThan(-1);
});

it("tells an empty list apart from an old result, and reports dropped findings without listing them", () => {
  const html = render(withFindings([], { confirmed_findings_dropped: 2 }));
  expect(html).toContain("정리된 확인 사실 없음");
  expect(html).not.toContain("이전 형식");
  expect(html).toContain("근거를 확인하지 못해 제외된 항목 2개");
});

it("ignores a stored entry that has no source or an unknown kind rather than showing it", () => {
  const html = render(withFindings([fact("근거 없는 문장", []), fact("종류를 모르는 문장", [spanA], "SOMETHING"), fact("정상 문장", [spanA])]));
  expect(html).toContain("정상 문장");
  expect(html).not.toContain("근거 없는 문장");
  expect(html).not.toContain("종류를 모르는 문장");
});

it("does not turn a stored fact into a claim that criteria are fulfilled", () => {
  const html = render(withFindings([fact("문서에 적힌 사실", [spanA])]));
  expect(html).toContain("문서에 적힌 내용이며 기준 충족을 뜻하지 않습니다");
});

