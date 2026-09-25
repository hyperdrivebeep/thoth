import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { UserActivityEvent } from "../api/research";
import { ResearchActivityStrip } from "./ResearchActivityStrip";

function event(seq: number, overrides: Partial<UserActivityEvent> = {}): UserActivityEvent {
  return {
    schema_version: "thoth.user_activity_event.v1",
    event_id: `activity:${seq}`,
    seq,
    severity: "success",
    visibility: "default",
    announce: "none",
    activity_kind: "source",
    action: "read",
    label_ko: `자료 ${seq} 확인`,
    state: "succeeded",
    research_relation: "read",
    redaction: { applied: false, classes: [], source_content_included: false },
    ...overrides,
  };
}

describe("ResearchActivityStrip", () => {
  it("shows only the five most recent default events in the compact view", () => {
    const html = renderToStaticMarkup(<ResearchActivityStrip events={Array.from({ length: 7 }, (_, index) => event(index + 1))} />);
    expect(html).not.toContain("자료 1 확인");
    expect(html).not.toContain("자료 2 확인");
    expect(html).toContain("자료 3 확인");
    expect(html).toContain("자료 7 확인");
    expect(html).toContain("5건");
  });

  it("keeps execution state separate from the research relationship", () => {
    const html = renderToStaticMarkup(<ResearchActivityStrip events={[event(1, { state: "succeeded", research_relation: "contradicts" })]} />);
    expect(html).toContain("실행 완료");
    expect(html).toContain("판단을 반박");
  });

  it("renders only sanitized tool and source detail when expanded", () => {
    const html = renderToStaticMarkup(<ResearchActivityStrip detailOpen events={[event(1, {
      why_ko: "근거 후보가 질문과 직접 연결되는지 확인합니다.",
      target: { kind: "span", title: "감사 보고서", display_ref: "연결 자료 1", safe_uri: "https://example.org", locator: { page: 6 }, source_version_id: "source-v3", hash_short: "71e8c8f2", currentness: "current", access_state: "allowed" },
      tool: { display_name: "연결 자료 읽기", family: "LOCAL", operation: "READ_SOURCE", sanitized_args: { page: 6 }, raw_command_available: false, command_detail: "unavailable" },
      result: { summary_ko: "지정한 쪽을 확인했습니다.", duration_ms: 840, counts: { spans: 1 } },
      redaction: { applied: true, classes: ["absolute_path", "source_content"], public_note_ko: "로컬 경로와 원문은 제외했습니다.", source_content_included: false },
      refs: { operation_alias: "operation:public", source_ref: "source:private-value" },
    })]} />);
    expect(html).toContain("연결 자료 읽기");
    expect(html).toContain("6쪽");
    expect(html).toContain("소요 840ms");
    expect(html).toContain("원시 명령은 이 화면에 제공되지 않습니다.");
    expect(html).toContain("로컬 경로와 원문은 제외했습니다.");
    expect(html).not.toContain("source:private-value");
  });

  it("renders nothing when the backend has no activity projection", () => {
    expect(renderToStaticMarkup(<ResearchActivityStrip events={[]} />)).toBe("");
  });
});
