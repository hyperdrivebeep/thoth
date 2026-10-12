// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it } from "vitest";
import { messages } from "../i18n/messages";
import { setLocale } from "../i18n/locale";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import trace from "./traceDemo.fixture.json";
import type { TraceView } from "../api/trace";
import { ExpectedResults } from "./ExpectedResults";
import { HypothesisCompare } from "./HypothesisCompare";
import { hypothesisRows } from "./hypothesisView";
import { testOrderInput } from "./testOrderInput";
import { rankItems } from "./testRanking";
import { nextStep, reasonSentence, verdictLook } from "./traceText";
import { investigationFor } from "./traceInvestigation";

afterEach(() => setLocale("ko"));

it("provides both languages for every static message without leaked translation calls", () => {
  for (const [key, value] of Object.entries(messages)) {
    expect(value.ko.trim(), key).not.toBe("");
    expect(value.en.trim(), key).not.toBe("");
    expect(value.ko + value.en, key).not.toMatch(/\bt\("/);
  }
});

it("changes expected-result labels but preserves source prose, row states and ordering", () => {
  const result = structuredClone(excerpt) as unknown as Record<string, unknown>;
  const rows = hypothesisRows(result);
  rows[0].statement = "한글 사용자 가설";
  const test = {expected_by_hypothesis: [
    {hypothesis_id:rows[0].id,knowledge_state:"PREDICTED",expected:"원문 값",basis:[]},
    {hypothesis_id:rows[1].id,knowledge_state:"UNKNOWN",expected:null,basis:["span:source"]},
    {hypothesis_id:rows[2].id,knowledge_state:"PREDICTED",expected:"stays same",basis:[]},
  ]};
  rows.forEach(row => { row.tests = []; });
  rows[0].tests = [{...test, test_id:"typed", estimated_cost:"1", reversibility:"FULL"}];
  const original = JSON.stringify({result, test});
  setLocale("ko");
  const before = rankItems(testOrderInput(rows, [], undefined).input);
  expect(before.groups[0].items[0].worst).toBe(2);
  expect(renderToStaticMarkup(<ExpectedResults test={test} rows={rows}/>)).toContain("모름");
  setLocale("en");
  const html = renderToStaticMarkup(<ExpectedResults test={test} rows={rows}/>);
  expect(html).toContain("Unknown");
  expect(html).toContain("Expected results for each hypothesis");
  expect(html).toContain("원문 값");
  expect(html).toContain("한글 사용자 가설");
  expect(rankItems(testOrderInput(rows, [], undefined).input)).toEqual(before);
  expect(JSON.stringify({result, test})).toBe(original);
  expect(renderToStaticMarkup(<HypothesisCompare result={result}/>)).toContain("Discriminating tests");
});

it("keeps verdict meaning and generated request language independent of display locale", () => {
  for (const view of [trace.phase1, trace.phase2] as unknown as TraceView[]) {
    for (const verdict of view.verdicts) {
      const kind = verdict.subject_kind === "REQUIREMENT" ? "REQUIREMENT" : "CRITERION";
      setLocale("ko");
      const before = verdictLook(kind, verdict.state);
      const question = investigationFor("p", kind, verdict, view, "ko")?.question;
      const checks = nextStep(kind, verdict.state, true)?.lines.length;
      setLocale("en");
      const after = verdictLook(kind, verdict.state);
      expect([after.intent, after.icon]).toEqual([before.intent, before.icon]);
      expect(after.text).not.toMatch(/[가-힣]/);
      const next = nextStep(kind, verdict.state, true);
      expect(next?.lines.length).toBe(checks);
      expect(JSON.stringify(next)).not.toMatch(/[가-힣]/);
      expect(investigationFor("p", kind, verdict, view, "ko")?.question).toBe(question);
      for (const code of verdict.reasons.computed) {
        expect(reasonSentence(code, id => id, verdict.conditions.condition)).not.toMatch(/[가-힣]/);
      }
    }
  }
});
