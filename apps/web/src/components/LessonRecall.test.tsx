import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import type { HypothesisLink } from "../api/hypothesisLink";
import type { LessonItem } from "../api/lessons";
import { HypothesisCompare } from "./HypothesisCompare";
import { LessonRecall } from "./LessonRecall";

const BANNED = /통과|해결|승인 완료|검증 완료|확률|순위|\d\s*%/;
const LEAK = /TEST_RESULT|ELIMINATION|EFFECT_CONFIRMED|HUMAN_CLOSURE|SAME_CONDITION|STALE|REFUTED|ALTERNATIVE|THIS_HYPOTHESIS|WAIVER|HUMAN_CLOSED|undefined|\bnull\b/;

const link = (hypothesisId: string, subject = "SYN-C-DET-RAIN"): HypothesisLink => ({
  hypothesis_id: hypothesisId, hypothesis_revision_digest: "d".repeat(64), statement: "s", subject_kind: "CRITERION", subject_id: subject, subject_title: "비 조건 탐지율",
  state: "CURRENT", change: null, link_state: "HOLD_NO_RESULT", current_state: "HOLD_NO_RESULT", current_verdict_revision: "r".repeat(64), recheck: null,
});
const lesson = (id: string, patch: Partial<LessonItem> = {}): LessonItem => ({
  lesson_id: id, kind: "TEST_RESULT", outcome: "ALTERNATIVE", state: "SAME_CONDITION", subject_kind: "CRITERION", subject_id: "SYN-C-DET-RAIN", hypothesis_id: "old-h", test_id: "t1",
  actor_id: "human:local-user", created_at: "2026-10-05T01:02:03+00:00",
  detail: { observations: [{ test_id: "t1", matched: "ALTERNATIVE", observation: "비 조건 결과가 맑은 조건과 같았다", evidence_refs: ["doc://run-7"] }] }, ...patch,
});
const render = (lessons: LessonItem[] | undefined, links: HypothesisLink[] | undefined = [link("now-h")], shown = ["now-h"]) =>
  renderToStaticMarkup(<LessonRecall links={links} lessons={lessons} shownIds={shown}/>);

it("shows an earlier recorded result of the same row, read back from its record, with who and when", () => {
  const html = render([lesson("a")]);
  expect(html).toContain("같은 조건의 이전 결과");
  expect(html).toContain("이전 시험 결과: 다른 설명과 맞음");
  expect(html).toContain("비 조건 결과가 맑은 조건과 같았다");
  expect(html).toContain("doc://run-7");
  expect(html).not.toMatch(BANNED);
  expect(html).not.toMatch(LEAK);
});

it("says plainly that it is a reference and not used for the order, and does not promise the same result", () => {
  const html = render([lesson("a")]);
  expect(html).toContain("순서에 쓰이지 않습니다");
  expect(html).toContain("같은 결과가 나온다는 뜻이 아닙니다");
  expect(html).toContain("같은 기준");
});

it("leaves out the lessons of the hypotheses on screen, of another row, and shows nothing without links or lessons", () => {
  const html = render([lesson("own", { hypothesis_id: "now-h", detail: { observations: [{ test_id: "t1", matched: "ALTERNATIVE", observation: "화면에 이미 있는 결과", evidence_refs: [] }] } }),
    lesson("other", { subject_id: "SYN-C-DET-FOG", detail: { observations: [{ test_id: "t1", matched: "ALTERNATIVE", observation: "안개 줄의 결과", evidence_refs: [] }] } })]);
  expect(html).toBe("");
  expect(render(undefined)).toBe("");
  expect(renderToStaticMarkup(<LessonRecall links={undefined} lessons={[lesson("a")]} shownIds={["now-h"]}/>)).toBe("");
  expect(render([lesson("a")], [])).toBe("");
});

it("counts the lessons it left out for being stale or refuted, without listing them", () => {
  const html = render([lesson("fresh"), lesson("old", { state: "STALE", detail: { observations: [{ test_id: "t1", matched: "ALTERNATIVE", observation: "낡은 결과 문장", evidence_refs: [] }] } }),
    lesson("against", { state: "REFUTED", detail: { observations: [{ test_id: "t1", matched: "ALTERNATIVE", observation: "반박된 결과 문장", evidence_refs: [] }] } })]);
  expect(html).toContain("낡음 1개");
  expect(html).toContain("반박됨 1개");
  expect(html).toContain("기록은 남아 있습니다");
  expect(html).not.toContain("낡은 결과 문장");
  expect(html).not.toContain("반박된 결과 문장");
});

it("tells apart a recorded closure, a confirmed effect and an elimination, with the original verdict and the document", () => {
  const html = render([
    lesson("c", { kind: "HUMAN_CLOSURE", outcome: "WAIVER_RECORDED", hypothesis_id: null, test_id: null, detail: { closure_kind: "WAIVER_RECORDED", basis_ref: "DEV-4", note: "", scope: null, original_state: "FAIL_COMPUTED" } }),
    lesson("e", { kind: "EFFECT_CONFIRMED", outcome: "CONFIRMED", hypothesis_id: null, test_id: null, actor_id: "system:lesson-rule", detail: { closure_kind: "FIX_APPLIED", basis_ref: "ECN-12", note: "", scope: null, original_state: "FAIL_COMPUTED" } }),
    lesson("x", { kind: "ELIMINATION", outcome: "REPEATED", test_id: null }),
  ]);
  expect(html).toContain("편차·면제 승인(원 기준 미충족 유지)");
  expect(html).toContain("근거 문서: DEV-4");
  expect(html).toContain("규칙이 계산");
  expect(html).toContain("ECN-12");
  expect(html).toContain("배제(반복 확인)");
  expect(html).toContain("원래 판정: 기준 미달");
  expect(html).not.toMatch(BANNED);
  expect(html).not.toMatch(LEAK);
});

it("lists the newest first and never ranks or counts the recalled ones", () => {
  const html = render([lesson("old", { created_at: "2026-10-01T00:00:00+00:00", detail: { observations: [{ test_id: "t1", matched: "NEITHER", observation: "먼저 기록한 결과", evidence_refs: [] }] } }),
    lesson("new", { created_at: "2026-10-05T00:00:00+00:00", detail: { observations: [{ test_id: "t1", matched: "NEITHER", observation: "나중에 기록한 결과", evidence_refs: [] }] } })]);
  expect(html.indexOf("나중에 기록한 결과")).toBeLessThan(html.indexOf("먼저 기록한 결과"));
  expect(html).not.toMatch(/\d+\s*(번|회|건) (확인|나온)/);
});

const result = () => structuredClone(excerpt) as unknown as Record<string, unknown>;
const hypothesisIds = () => (excerpt as unknown as { portfolio: { hypotheses: { hypothesis_id: string }[] } }).portfolio.hypotheses.map(item => item.hypothesis_id);
it("does not change the order of the tests: the ranking never reads a lesson", () => {
  const links = hypothesisIds().map(id => link(id));
  const plain = renderToStaticMarkup(<HypothesisCompare result={result()} projectId="p" links={links}/>);
  const withLessons = renderToStaticMarkup(<HypothesisCompare result={result()} projectId="p" links={links} lessons={[lesson("a"), lesson("b", { state: "REFUTED" })]}/>);
  const section = (html: string) => html.match(/<section class="detail-card test-order"[\s\S]*?<\/section>/)![0];
  expect(withLessons).toContain("같은 조건의 이전 결과");
  expect(plain).not.toContain("같은 조건의 이전 결과");
  expect(section(withLessons)).toBe(section(plain));
});
