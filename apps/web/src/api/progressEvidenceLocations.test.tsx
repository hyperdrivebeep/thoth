import { afterEach, describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ResearchLiveProgress } from "../components/ResearchLiveProgress";
import { setLocale } from "../i18n/locale";
import type { ResearchStatus, UserActivityEvent, UserActivityTarget } from "./research";
import { composerActivity, evidenceLabels, liveResearchProgress } from "./researchProgress";

afterEach(() => setLocale("ko"));

function source(id: string, locator: UserActivityTarget["locator"], access: UserActivityTarget["access_state"] = "allowed"): UserActivityEvent {
  return { schema_version: "thoth.user_activity_event.v1", event_id: `event:${id}`, seq: 1,
    severity: "info", visibility: "default", announce: "none", activity_kind: "source", action: "read",
    label_ko: "연결된 자료를 읽었습니다.", state: "succeeded", research_relation: "read",
    target: { kind: "span", title: "연결된 자료", locator, access_state: access },
    refs: { source_ref: `source:${id}` }, redaction: { applied: true, source_content_included: false },
  };
}

function status(text: string, activity?: UserActivityEvent[]): ResearchStatus {
  return { project_id: "p", thread_id: "t", cycle_id: "c", problem: "Inspect the test result",
    lifecycle: "ACTIVE", execution_state: "RUNNING", current_object_ids: [], working_head_digest: "0".repeat(64),
    operation_state: "RUNNING", attempt: { operation_id: "o", phase: "EVIDENCE_FOCUS", status: "RUNNING",
      draft_progress: { evidence_focus: { locators: [{ span_id: "span:1", page: 6, exact_text: text }], total: 1 } } },
    completed_stages: [{ role: "EVIDENCE_RERANKER", state: "COMPLETED" }],
    activity_events: [{ seq: 7, kind: "action", action_type: "source_read", payload: { span_id: "span:1", page: 6, exact_text: text } }],
    ...(activity === undefined ? {} : { user_activity_events: activity }),
  };
}

const texts = [
  "# SYNTHETIC DEMO DATA - NOT MEASURED - NOT APPROVED - NOT FOR ENGINEERING USE",
  '- {id: SAMPLE, title: Long YAML criterion, threshold: "0.50", unit: per_min}',
  "case,condition,numerator,denominator,value\nA,rain,16,20,0.80",
  "일반 사용자 파일의 긴 첫 줄과 표 설명입니다. ".repeat(12),
];

describe.each(["ko", "en"] as const)("progress-only source presentation (%s)", language => {
  it.each(texts)("does not render the original excerpt: %s", text => {
    setLocale(language);
    const input = status(text, [source("1", { line_start: 7, line_end: 7 })]);
    const saved = structuredClone(input);
    const live = liveResearchProgress(input)!;
    expect(live.evidenceLabels).toEqual([language === "ko" ? "7행" : "line 7"]);
    const html = renderToStaticMarkup(<ResearchLiveProgress status={input}/>);
    expect(html).not.toContain(text.split("\n")[0]);
    expect(JSON.stringify(live)).not.toContain(text.split("\n")[0]);
    expect(live.events.find(event => event.actionType === "source_read")?.payload).toBe(language === "ko" ? "위치 미확인" : "Location unavailable");
    expect(composerActivity(input)).toBe(live.headline);
    expect(input).toEqual(saved);
  });

  it("keeps old payloads readable without falling back to source text", () => {
    setLocale(language);
    const input = status("ORIGINAL_SOURCE_MUST_STAY_STORED");
    expect(evidenceLabels(input)).toEqual(["p.6"]);
    const live = liveResearchProgress(input)!;
    expect(live.events.find(event => event.actionType === "source_read")).toMatchObject({ id: "event-action-7", payload: "p.6" });
    expect(JSON.stringify(live)).not.toContain("ORIGINAL_SOURCE_MUST_STAY_STORED");
    input.attempt!.draft_progress = { evidence_focus: { locators: [{ exact_text: "ORIGINAL_SOURCE_MUST_STAY_STORED" }], total: 1 } };
    expect(evidenceLabels(input)).toEqual([language === "ko" ? "원문 위치 1" : "Source location 1"]);
  });

  it.each(["blocked", "out_of_scope", "unknown"] as const)("does not revive location metadata for %s access", access => {
    setLocale(language);
    const input = status("SECRET_SOURCE", [source("1", { page: 99, line_start: 777, cell: "A3" }, access)]);
    const live = liveResearchProgress(input)!;
    expect(live.evidenceLabels).toEqual([language === "ko" ? "위치 미확인" : "Location unavailable"]);
    expect(JSON.stringify(live)).not.toMatch(/SECRET_SOURCE|p\.99|777|A3|p\.6/);
  });
});

it("does not match typed sources to legacy source actions by array position", () => {
  const input = status("ORIGINAL", [source("different", { page: 99 })]);
  const action = liveResearchProgress(input)!.events.find(event => event.actionType === "source_read")!;
  expect(action).toMatchObject({ id: "event-action-7", payload: "위치 미확인" });
});

it("keeps distinct sources with the same location and deduplicates repeated identity", () => {
  const input = status("ORIGINAL", [source("a", { line_start: 7 }), source("b", { line_start: 7 }), source("a", { line_start: 7 })]);
  expect(evidenceLabels(input)).toEqual(["7행", "7행"]);
});

it("treats conflicting or denied copies of the same source conservatively", () => {
  const input = status("ORIGINAL", [source("a", { page: 7 }), source("a", { page: 7 }, "blocked")]);
  expect(evidenceLabels(input)).toEqual(["위치 미확인"]);
});

it("uses at most two bounded locations in both current and completed-stage titles", () => {
  const input = status("ORIGINAL", [source("a", { page: 1 }), source("b", { page: 2 }), source("c", { page: 3 })]);
  input.attempt!.draft_progress = { evidence_focus: { total: 18, truncated: true } };
  const live = liveResearchProgress(input)!;
  expect(live.evidenceLabels).toEqual(["p.1", "p.2", "p.3"]);
  expect(live.headline).toContain("p.1, p.2");
  expect(live.headline).not.toContain("p.3");
  expect(live.events.filter(e => e.kind === "note" && !e.live).every(e => !e.text.includes("p.3"))).toBe(true);
  const html = renderToStaticMarkup(<ResearchLiveProgress status={input}/>);
  expect(html).toContain("외 1곳");
  expect(html).toContain("전체 18곳 중 3곳 표시");
  input.user_activity_events = [source("large", { page: Number.MAX_SAFE_INTEGER, line_start: 1, line_end: Number.MAX_SAFE_INTEGER, cell: `AAAA${"9".repeat(60)}` })];
  expect(evidenceLabels(input)[0].length).toBeLessThanOrEqual(64);
  expect(evidenceLabels(input)[0]).toMatch(/…$/);
});

it("does not turn missing typed metadata into an empty corpus or use unsafe fields", () => {
  const input = status("ORIGINAL", []);
  expect(evidenceLabels(input)).toEqual(["위치 미확인"]);
  input.user_activity_events = [source("bad", { page: NaN, line_start: -2, cell: "A1; ORIGINAL", section: "ORIGINAL" })];
  input.user_activity_events[0].target!.title = "ORIGINAL";
  input.user_activity_events[0].target!.display_ref = "ORIGINAL";
  expect(JSON.stringify(liveResearchProgress(input))).not.toContain("ORIGINAL");
  input.attempt!.draft_progress = {};
  input.user_activity_events = [];
  expect(liveResearchProgress(input)!.missing).toBe("위치 미확인");
});

it("does not reread exact_text when rendering the old response", () => {
  const input = status("ORIGINAL");
  input.attempt!.draft_progress = { evidence_focus: { total: 1, locators: [{ page: 6, get exact_text() { throw new Error("Original source was read by progress UI"); } }] } };
  expect(evidenceLabels(input)).toEqual(["p.6"]);
});

it.each(["ko", "en"] as const)("formats validated page, line range and cell coordinates (%s)", language => {
  setLocale(language);
  const input = status("ORIGINAL", [source("range", { page: 6, line_start: 2, line_end: 5, cell: "$A$1:C3", section: "ORIGINAL" })]);
  expect(evidenceLabels(input)).toEqual([language === "ko" ? "p.6 · 2~5행 · $A$1:C3" : "p.6 · lines 2–5 · $A$1:C3"]);
});

it("does not trust an unsupported typed schema or fall back to its old page", () => {
  const event = { ...source("next", { page: 99 }), schema_version: "unknown-version" } as unknown as UserActivityEvent;
  expect(evidenceLabels(status("ORIGINAL", [event]))).toEqual(["위치 미확인"]);
});
