import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it } from "vitest";
import excerpt from "../api/fixtures/iris-judgment-excerpt.json";
import { setLocale } from "../i18n/locale";
import { ResearchResultCard } from "./ResearchResultCard";
import { ResultStatusStrip } from "./ResultStatusStrip";
import { interruptionView } from "./resultInterruption";
import { parseAnswer, splitAnswerLead } from "./answerText";

afterEach(() => setLocale("ko"));
const digest = "a".repeat(64);
const row = (status: "SATISFIED" | "UNRESOLVED" | "NOT_ASSESSED") => ({ requirement_id: "r-" + status + Math.random(), target: "RESEARCH_GAP", question: "q", status,
  applicability: "APPLICABLE", relation: "QUALIFIES", validation: "APPLIED", blocker: "", review_refs: [], evidence_refs: [], reason_codes: [] });
const coverage = (rows: ReturnType<typeof row>[], availability: "AVAILABLE" | "UNAVAILABLE" = "AVAILABLE") => ({ schema_version: "1.0.0" as const, request_revision_digest: digest,
  availability, reason_codes: [], requirement_set_revision_digest: null, coverage_revision_digest: null, rows,
  summary: { satisfied: 0, unresolved: 0, not_applicable: 0, not_assessed: 0, hold_targets: [], reason_codes: [] } });
const strip = (result: Record<string, unknown>, cov?: ReturnType<typeof coverage>) => renderToStaticMarkup(<ResultStatusStrip result={result} coverage={cov}/>);
const visible = (html: string) => html.replace(/<[^>]+>/g, "|");

it("shows the conclusion, criteria met over total, the counter-evidence search and the source count as header tags from the server's numbers", () => {
  const html = strip({ ...excerpt, answer_status: "PARTIAL_HOLD", selected_evidence_count: 21 }, coverage([row("SATISFIED"), row("UNRESOLVED"), row("NOT_ASSESSED")]));
  const text = visible(html);
  expect(text).toContain("판단 보류");
  expect(text).toContain("기준 3개 중 충족 1 · 확인 필요 2");
  expect(text).toContain("반대 근거 안 찾음");
  expect(text).toContain("근거 21곳");
});

it("leaves out a count it does not have instead of showing zero, and renders nothing for a result without status", () => {
  const text = visible(strip({ answer: "a", answer_status: "PARTIAL_HOLD" }, coverage([row("UNRESOLVED")], "UNAVAILABLE")));
  expect(text).toContain("판단 보류");
  expect(text).not.toContain("기준 ");
  expect(text).not.toContain("근거 ");
  expect(strip({ answer: "a" })).toBe("");
});

it("keeps the unresolved count in the header while the review basis is folded", () => {
  const html = renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{ answer: "a", answer_status: "PARTIAL_HOLD" }} coverageMatrix={coverage([row("UNRESOLVED"), row("UNRESOLVED")])} onDetail={() => {}}/>);
  const header = html.slice(0, html.indexOf("answer-text"));
  expect(visible(header)).toContain("기준 2개 중 충족 0 · 확인 필요 2");
  expect(html).toContain("검토 근거: 판단 보류 · 기준 2개와 남은 확인 사항");
  expect(html).toContain("답변 점검 항목 2개 중 충족 0 · 확인 필요 2");
});

it("words the tags in every screen language", () => {
  for (const [locale, expected] of [["en", "3 criteria · 1 met · 2 to check"], ["zh-Hans", "共 3 项标准 · 已满足 1 · 需要检查 2"], ["ja", "基準 3 件 · 満たす 1 · 確認が必要 2"]] as const) {
    setLocale(locale);
    expect(visible(strip({ answer: "a", answer_status: "PARTIAL_HOLD" }, coverage([row("SATISFIED"), row("UNRESOLVED"), row("NOT_ASSESSED")])))).toContain(expected);
  }
});

it("treats a SUCCEEDED operation whose terminal reason is a stalled model call as interrupted, and a bounded completion as finished", () => {
  const stalled = interruptionView({ state: "SUCCEEDED", result: { answer: "复核暂缓", terminal_reason: "OAUTH_STALLED_STREAM_REMOTE_STOP_UNKNOWN" }, terminalReason: "OAUTH_STALLED_STREAM_REMOTE_STOP_UNKNOWN" });
  expect(stalled.interrupted).toBe(true);
  expect(stalled.cutOffKind).toBe("slow");
  expect(stalled.cutOff).toBe(false); // an answer was saved, so it is not reported as "no answer"
  const done = interruptionView({ state: "SUCCEEDED", result: { answer: "a", terminal_reason: "BOUNDED_RESEARCH_COMPLETE" }, terminalReason: "BOUNDED_RESEARCH_COMPLETE" });
  expect(done.interrupted).toBe(false);
  expect(interruptionView({ state: "FAILED", result: null }).interrupted).toBe(true);
});

it("takes the hold lead off the answer in every answer language so a heading right after it is read as a heading", () => {
  for (const lead of ["검증 보류 · 부분 초안: ", "Review on hold · partial draft: ", "复核暂缓 · 部分草稿：", "検証保留・部分草稿："]) {
    const { lead: found, body } = splitAnswerLead(lead + "## 事实\n- 첫째\n- 둘째\n\n본문 (span:aaaa1111)");
    expect(found).toBe(lead.trim());
    const parsed = parseAnswer(body);
    expect(parsed.blocks[0]).toMatchObject({ kind: "p", heading: 2 });
    expect(parsed.blocks[0].kind === "p" && parsed.blocks[0].inline[0].kind === "text" && parsed.blocks[0].inline[0].text).toBe("事实");
    expect(parsed.blocks[1].kind).toBe("ul");
    expect(parsed.citations).toHaveLength(1);
  }
  expect(splitAnswerLead("그냥 답")).toEqual({ lead: null, body: "그냥 답" });
  const html = renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{ answer: "検証保留・部分草稿：### 事実\nLAB-42は合成実験記録です。", answer_status: "PARTIAL_HOLD" }} onDetail={() => {}}/>);
  expect(html).toContain('<p class="answer-lead"><strong>検証保留・部分草稿：</strong></p>');
  expect(html).toContain('<h4 class="answer-heading">事実</h4>');
  expect(html).not.toContain("#");
});
