import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { composerActivity, liveResearchProgress } from "./researchProgress";
import { ResearchLiveProgress } from "../components/ResearchLiveProgress";
import { timelineExampleStatus } from "./timelineExample";
import type { ResearchStatus } from "./research";

function running(overrides: Partial<ResearchStatus> = {}): ResearchStatus {
  return {
    thread_id: "thread:t",
    project_id: "project:p",
    cycle_id: "cycle:c",
    problem: "q",
    lifecycle: "ACTIVE",
    execution_state: "RUNNING",
    current_object_ids: [],
    working_head_digest: "0".repeat(64),
    operation_state: "RUNNING",
    attempt: { operation_id: "operation:o", phase: "HYPOTHESIS_REVIEW", status: "RUNNING" },
    completed_stages: [{ role: "RESEARCH_PLANNER", state: "COMPLETED", elapsed_ms: 91700, context_bytes: 12000 }],
    ...overrides,
  };
}

describe("live research progress", () => {
  it("exposes Korean scenes while RUNNING and keeps them after HOLD", () => {
    const live = liveResearchProgress(running());
    expect(live?.headline).toBe("방금 만든 설명을 원문과 대조하고 있습니다");
    expect(live?.events[0]).toMatchObject({ kind: "note", text: "질문에서 꼭 지킬 조건을 고정했습니다" });
    expect(live?.events[1]).toMatchObject({ kind: "action", label: "단계 완료", payload: "1분 32초 · 맥락 12KB" });
    expect(live?.evidenceLabels).toEqual([]);
    const held = liveResearchProgress(
      running({
        operation_state: "SUCCEEDED",
        problem: "Psyche 표 3 발사 준비 조건",
        attempt: { operation_id: "operation:o", phase: "HOLD", status: "SUCCEEDED" },
        current_result: {
          operation_id: "operation:o",
          phase: "HOLD",
          state: "PARTIAL",
          completion: "TERMINAL",
          terminal_reason: "XAI_AUTH_REQUIRED_403",
          basis_digest: "0".repeat(64),
          result: {},
          gaps: ["XAI_AUTH_REQUIRED_403"],
          next_steps: ["XAI_AUTH_REQUIRED_403"],
          source_refs: ["span:1"],
          record_refs: [],
        },
        activity_events: [
          { seq: 1, kind: "note", key: "HOLD", live: true },
          {
            seq: 2,
            kind: "action",
            action_type: "model_call",
            live: false,
            payload: { elapsed_ms: 173, received_bytes: 0, state: "OBSERVED" },
          },
        ],
      }),
      ["C:/papers/psyche-irb-2022.pdf"],
    );
    expect(held?.headline).toContain("연구를 멈췄습니다");
    expect(held?.missing).toContain("인증이 거절");
    expect(held?.events.some((event) => event.live)).toBe(false);
    expect(held?.events.some((event) => event.actionType === "model_call")).toBe(true);
    const unsupported = liveResearchProgress(
      running({
        operation_state: "SUCCEEDED",
        attempt: { operation_id: "operation:o", phase: "HOLD", status: "SUCCEEDED" },
        current_result: {
          operation_id: "operation:o",
          phase: "HOLD",
          state: "PARTIAL",
          completion: "TERMINAL",
          terminal_reason: "OAUTH_MODEL_NOT_SUPPORTED_400",
          basis_digest: "0".repeat(64),
          result: {},
          gaps: ["OAUTH_MODEL_NOT_SUPPORTED_400"],
          next_steps: ["OAUTH_MODEL_NOT_SUPPORTED_400"],
          source_refs: ["span:1"],
          record_refs: [],
        },
      }),
    );
    expect(unsupported?.missing).toContain("지원하지 않는 모델");
  });

  it("keeps repeated roles in time order instead of grouping them", () => {
    const live = liveResearchProgress(
      running({
        completed_stages: [
          { role: "SEMANTIC_REVIEWER", elapsed_ms: 4000, context_bytes: 2048 },
          { role: "SEMANTIC_REVIEWER", elapsed_ms: 8000, context_bytes: 4096 },
        ],
      }),
    );
    const notes = live?.events.filter((event) => event.kind === "note" && !event.live).map((event) => event.text);
    const payloads = live?.events.filter((event) => event.actionType === "stage_completed").map((event) => event.payload);
    expect(notes).toEqual(["질문 조건을 원문과 대조했습니다", "질문 조건을 원문과 대조했습니다"]);
    expect(payloads).toEqual(["4초 · 맥락 2.0KB", "8초 · 맥락 4.0KB"]);
    expect(notes?.join(" ")).not.toContain("2회");
  });

  it("names the actual constraints, locators, and hypotheses in the notes", () => {
    const live = liveResearchProgress(timelineExampleStatus, [
      "C:/papers/psyche-irb-2022.pdf",
      "C:/papers/gao-23-106021.pdf",
    ]);
    const notes = live?.events.filter((event) => event.kind === "note").map((event) => event.text) ?? [];
    expect(notes[0]).toContain("표 3 발사 준비 조건 4개");
    expect(notes[0]).toContain("표 숫자만 인용");
    expect(notes[0]).toContain("추측 금지");
    expect(notes[1]).toContain("p.6 70.6");
    expect(notes[1]).toContain("GNC software verification complete");
    expect(notes[2]).toContain("표 3 발사 준비 조건 4개");
    expect(notes[2]).toContain("p.6 70.6");
    expect(notes[3]).toContain("일정만 늘리면 해결된다");
    expect(notes[3]).toContain("GNC 소프트웨어만 닫으면 된다");
    expect(notes.at(-1)).toContain("일정만 늘리면 해결된다");
    expect(notes.at(-1)).toContain("빠진 결정을 채우겠습니다");
    expect(live?.headline).toContain("원문과 대조하고 있습니다");
    expect(live?.headline).not.toContain("질문 조건을 고정했습니다");
    expect(live?.headline).not.toContain("가능한 설명을 여러 개");
  });

  it("renders finished work, current action, and source filenames without role codes", () => {
    const html = renderToStaticMarkup(
      <ResearchLiveProgress
        status={running({
          problem: "Psyche 표 3 발사 준비 조건",
          attempt: {
            operation_id: "operation:o",
            phase: "HYPOTHESIS_REVIEW",
            status: "RUNNING",
            draft_progress: {
              evidence_focus: {
                locators: [{ page: 6, exact_text: "70.6" }],
              },
              portfolio: {
                hypotheses: [
                  { hypothesis_id: "h1", statement: "일정만 늘리면 해결된다" },
                  { hypothesis_id: "h2", statement: "GNC 소프트웨어만 닫으면 된다" },
                  { hypothesis_id: "h3", statement: "시험환경·V&V·운용 준비를 함께 복구해야 한다" },
                  { hypothesis_id: "h4", statement: "인력과 감독 공백이 발사 준비를 막고 있다" },
                ],
              },
              hypothesis_review: { decisions: [{ hypothesis_id: "h1" }, { hypothesis_id: "h2" }] },
            },
          },
          request: {
            operation_id: "operation:o",
            authored_text:
              "Psyche 표 3 발사 준비 조건 4개가 원문에서 각각 확인되는지 적어라. 표 숫자만 인용하고 추측 금지.",
            model_settings: { model: "grok-4.6", reasoning_effort: "high" },
          },
          usage: { input_tokens: 8000, output_tokens: 4400, total_tokens: 12400, state: "PARTIAL", unreported_calls: 1, cumulative_token_limit_enforced: false },
          model_dispatches: [{ state: "RESERVED", transport_observation: { elapsed_ms: 47000, received_bytes: 0 } }],
        })}
        sourceUris={["C:/papers/mobilenets.pdf"]}
      />,
    );
    expect(html).toContain("표 3 발사 준비 조건 4개");
    expect(html).toContain("표 숫자만 인용");
    expect(html).toContain("추측 금지");
    expect(html).toContain("p.6 70.6");
    expect(html).toContain("일정만 늘리면 해결된다");
    expect(html).toContain("GNC 소프트웨어만 닫으면 된다");
    expect(html).toContain("빠진 결정을 채우겠습니다");
    expect(html).toContain("작업 상세");
    expect(html).not.toContain("단계 완료");
    expect(html).not.toContain("1분 32초");
    expect(html).not.toContain("grok-4.6 · 추론 high");
    expect(html).not.toContain("사용 토큰 12,400 (부분 관측)");
    expect(html).not.toContain("모델 호출");
    expect(html).not.toContain("질문 조건을 고정했습니다");
    expect(html).not.toContain("가능한 설명을 여러 개");
    expect(html).not.toContain("RESEARCH_PLANNER");
    expect(html).not.toContain("HYPOTHESIS_REVIEW");
    expect(html).toContain('role="status"');
  });

  it("says local corpus is empty during evidence focus instead of implying a web search", () => {
    const html = renderToStaticMarkup(
      <ResearchLiveProgress
        status={running({
          attempt: {
            operation_id: "operation:o",
            phase: "EVIDENCE_FOCUS",
            status: "RUNNING",
            draft_progress: { evidence_focus: { locators: [], total: 0 } },
          },
          completed_stages: [
            { role: "RESEARCH_PLANNER", state: "COMPLETED", elapsed_ms: 91700 },
            { role: "EVIDENCE_RERANKER", state: "COMPLETED", elapsed_ms: 59100 },
          ],
        })}
      />,
    );
    expect(html).toContain("표와 원문 위치를 확인하고 있습니다");
    expect(html).toContain("질문과 관련된 문장을 다시 줄 세웠습니다");
    expect(html).toContain("집어넣을 표·문장이 아직 0곳입니다");
    expect(html).not.toContain("CONNECTED_SOURCES");
    expect(html).not.toContain("EVIDENCE_FOCUS");
  });

  it("names source shortlist as choosing local excerpts, not a web search", () => {
    const live = liveResearchProgress(
      running({
        problem: "Psyche 표 3 발사 준비 조건",
        attempt: { operation_id: "operation:o", phase: "SOURCE_SHORTLIST", status: "RUNNING" },
        completed_stages: [],
      }),
      ["C:/papers/psyche-irb-2022.pdf"],
    );
    expect(live?.headline).toContain("원문 구간을 고르고 있습니다");
    expect(live?.headline).toContain("psyche-irb-2022.pdf");
    expect(live?.missing).toContain("답을 쓰기 전입니다");
    expect(live?.current.detail).toContain("시간 미확인");
  });

  it("uses published activity events when the server already projected them", () => {
    const live = liveResearchProgress(
      running({
        activity_events: [
          { seq: 1, kind: "note", key: "RESEARCH_PLANNER", live: false },
          {
            seq: 2,
            kind: "action",
            action_type: "stage_completed",
            live: false,
            payload: { elapsed_ms: 91700, context_bytes: 12000, dispatch_count: 1 },
          },
          { seq: 3, kind: "note", key: "HYPOTHESIS_REVIEW", live: true },
          {
            seq: 4,
            kind: "action",
            action_type: "model_call",
            live: true,
            payload: { state: "RESERVED", elapsed_ms: 47000, received_bytes: 0 },
          },
        ],
      }),
    );
    expect(live?.events.map((event) => [event.kind, event.text, event.payload ?? event.live])).toEqual([
      ["note", "질문에서 꼭 지킬 조건을 고정했습니다", false],
      ["action", "단계 완료", "1분 32초 · 맥락 12KB · 모델 1회"],
      ["note", "방금 만든 설명을 원문과 대조하고 있습니다", true],
      ["action", "모델 호출", "47초 · 첫 글자 아직 없음"],
    ]);
  });

  it("uses the same Korean headline in the composer footer", () => {
    expect(
      composerActivity(
        running({
          attempt: { operation_id: "operation:o", phase: "EVIDENCE_FOCUS", status: "RUNNING" },
        }),
      ),
    ).toBe("표와 원문 위치를 확인하고 있습니다");
    expect(composerActivity(running({ execution_state: "PAUSED" }))).toBe("작업이 일시정지되어 있습니다.");
  });
});
