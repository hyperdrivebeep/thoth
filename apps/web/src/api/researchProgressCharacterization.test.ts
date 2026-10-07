import { describe, expect, it } from "vitest";

import { composerActivity, evidenceLabels, formatDuration, liveResearchProgress } from "./researchProgress";
import type { ResearchStatus } from "./research";

/**
 * Pins the wording that researchProgress produces today (Korean particles, durations, sizes, scene
 * sentences, one-line texts), so that moving the text code into its own file changes nothing.
 * Only the public exports are used; the expected values were taken from the code before the move.
 */

function status(overrides: Record<string, unknown> = {}): ResearchStatus {
  return {
    thread_id: "thread:t",
    project_id: "project:p",
    cycle_id: "cycle:c",
    problem: "Psyche 표 3 발사 준비 조건",
    lifecycle: "ACTIVE",
    execution_state: "RUNNING",
    current_object_ids: [],
    working_head_digest: "0".repeat(64),
    operation_state: "RUNNING",
    attempt: { operation_id: "operation:o", phase: "RESEARCH_PLANNER", status: "RUNNING" },
    completed_stages: [],
    ...overrides,
  } as ResearchStatus;
}

function inPhase(phase: string, extra: Record<string, unknown> = {}, draft: Record<string, unknown> = {}) {
  return status({
    attempt: { operation_id: "operation:o", phase, status: "RUNNING", draft_progress: draft },
    ...extra,
  });
}

const RICH_DRAFT = {
  evidence_focus: { locators: [{ page: 6, exact_text: "70.6" }, { page: 8, exact_text: "GNC" }], total: 5 },
  portfolio: {
    hypotheses: [
      { hypothesis_id: "h1", statement: "일정만 늘리면 해결된다" },
      { hypothesis_id: "h2", statement: "GNC 소프트웨어만 닫으면 된다" },
      { hypothesis_id: "h3", statement: "인력 공백" },
    ],
  },
  hypothesis_review: { decisions: [{ hypothesis_id: "h1" }] },
};
const QUESTION = {
  request: {
    operation_id: "operation:o",
    authored_text: "Psyche 표 3 발사 준비 조건 4개가 원문에서 각각 확인되는지 적어라. 표 숫자만 인용하고 추측 금지. 원문 위치와 함께 답.",
  },
};
const SOURCES = ["C:/papers/psyche-irb-2022.pdf", "C:/papers/gao-23-106021.pdf", "C:/papers/third.pdf"];

const KEYS = [
  "RESEARCH_PLANNER", "REQUIREMENTS", "EVIDENCE_RERANKER", "SEMANTIC_REVIEWER", "EVIDENCE_REVIEW",
  "REVIEW_ADJUDICATOR", "SOURCE_PLANNER", "HYPOTHESIS_GENERATOR", "HYPOTHESES", "HYPOTHESIS_REVIEWER",
  "HYPOTHESIS_REVIEW", "ACTION_PLANNER", "ACTION_COMPARISON", "EVIDENCE_EXTRACTOR", "EVIDENCE_FOCUS",
  "SUFFICIENCY_EXPLAINER", "COUNTEREVIDENCE_CHALLENGER", "USER_EXPLAINER", "REFERENCE_MAPPER",
  "CONNECTED_SOURCES", "SOURCE_SHORTLIST", "HOLD", "SOMETHING_ELSE",
];

describe("durations", () => {
  it.each([
    [null, null], [undefined, null], [Number.NaN, null], [-1, null], [Number.POSITIVE_INFINITY, null],
    [0, "0ms"], [999, "999ms"], [1000, "1초"], [1499, "1초"], [59499, "59초"], [59500, "1분"],
    [60000, "1분"], [61000, "1분 1초"], [91700, "1분 32초"], [3600000, "60분"],
  ])("formats %s as %s", (input, expected) => {
    expect(formatDuration(input as number | null | undefined)).toBe(expected);
  });
});

describe("scene sentences", () => {
  it("pins the running and finished sentence of every phase with the facts of a rich question", () => {
    const lines: string[] = [];
    for (const key of KEYS) {
      for (const state of ["RUNNING", "SUCCEEDED"]) {
        const live = liveResearchProgress(inPhase(key, { operation_state: state, ...QUESTION }, RICH_DRAFT), SOURCES);
        lines.push(`${key} ${state}: ${live?.headline} // ${live?.current.detail} // ${live?.missing}`);
      }
    }
    expect(lines).toMatchInlineSnapshot(`
      [
        "RESEARCH_PLANNER RUNNING: 표 3 발사 준비 조건 4개, 표 숫자만 인용, 추측 금지를 고정하고 있습니다 // 아직 답을 쓰지 않았습니다. // null",
        "RESEARCH_PLANNER SUCCEEDED: 표 3 발사 준비 조건 4개, 표 숫자만 인용, 추측 금지를 고정했습니다 // 아직 답을 쓰지 않았습니다. // null",
        "REQUIREMENTS RUNNING: 표 3 발사 준비 조건 4개, 표 숫자만 인용, 추측 금지를 고정하고 있습니다 // 아직 답을 쓰지 않았습니다. // null",
        "REQUIREMENTS SUCCEEDED: 표 3 발사 준비 조건 4개, 표 숫자만 인용, 추측 금지를 고정했습니다 // 아직 답을 쓰지 않았습니다. // null",
        "EVIDENCE_RERANKER RUNNING: p.6 70.6, p.8 GNC와 관련된 문장을 다시 줄 세우고 있습니다 // 순위만 매겼고, 지지 여부는 아직 판단하지 않았습니다. // null",
        "EVIDENCE_RERANKER SUCCEEDED: p.6 70.6, p.8 GNC와 관련된 문장을 다시 줄 세웠습니다 // 순위만 매겼고, 지지 여부는 아직 판단하지 않았습니다. // null",
        "SEMANTIC_REVIEWER RUNNING: 표 3 발사 준비 조건 4개가 p.6 70.6, p.8 GNC 원문에 있는지 대조하고 있습니다 // 관련 있다고 해서 지지하는 것은 아닙니다. // null",
        "SEMANTIC_REVIEWER SUCCEEDED: 표 3 발사 준비 조건 4개가 p.6 70.6, p.8 GNC 원문에 있는지 대조했습니다 // 관련 있다고 해서 지지하는 것은 아닙니다. // null",
        "EVIDENCE_REVIEW RUNNING: 표 3 발사 준비 조건 4개가 p.6 70.6, p.8 GNC 원문에 있는지 대조하고 있습니다 // 관련 있다고 해서 지지하는 것은 아닙니다. // null",
        "EVIDENCE_REVIEW SUCCEEDED: 표 3 발사 준비 조건 4개가 p.6 70.6, p.8 GNC 원문에 있는지 대조했습니다 // 관련 있다고 해서 지지하는 것은 아닙니다. // null",
        "REVIEW_ADJUDICATOR RUNNING: Psyche 표 3 발사 준비 조건 중 빠진 항목과 충돌을 판정하고 있습니다 // 애매한 항목은 보류로 남깁니다. // null",
        "REVIEW_ADJUDICATOR SUCCEEDED: Psyche 표 3 발사 준비 조건 중 빠진 항목과 충돌을 판정했습니다 // 애매한 항목은 보류로 남깁니다. // null",
        "SOURCE_PLANNER RUNNING: psyche-irb-2022.pdf, gao-23-106021.pdf 외에 더 열 주소를 고르고 있습니다 // 인터넷 전체를 검색하지 않습니다. // null",
        "SOURCE_PLANNER SUCCEEDED: psyche-irb-2022.pdf, gao-23-106021.pdf 외에 더 열 주소를 골랐습니다 // 인터넷 전체를 검색하지 않습니다. // null",
        "HYPOTHESIS_GENERATOR RUNNING: 「일정만 늘리면 해결된다」, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」을 준비하고 있습니다 // 아직 채택이 아닙니다. // null",
        "HYPOTHESIS_GENERATOR SUCCEEDED: 「일정만 늘리면 해결된다」, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」을 준비했습니다 // 아직 채택이 아닙니다. // null",
        "HYPOTHESES RUNNING: 「일정만 늘리면 해결된다」, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」을 준비하고 있습니다 // 아직 채택이 아닙니다. 「일정만 늘리면 해결된다」는 봤고, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」 2개는 아직입니다. 빠진 결정을 채우겠습니다. // 「일정만 늘리면 해결된다」는 봤고, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」 2개는 아직입니다. 빠진 결정을 채우겠습니다.",
        "HYPOTHESES SUCCEEDED: 「일정만 늘리면 해결된다」, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」을 준비했습니다 // 아직 채택이 아닙니다. 「일정만 늘리면 해결된다」는 봤고, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」 2개는 아직입니다. 빠진 결정을 채우겠습니다. // 「일정만 늘리면 해결된다」는 봤고, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」 2개는 아직입니다. 빠진 결정을 채우겠습니다.",
        "HYPOTHESIS_REVIEWER RUNNING: 「일정만 늘리면 해결된다」를 원문과 대조하고 있습니다 // 제안된 설명마다 결정이 있어야 검토가 끝납니다. // null",
        "HYPOTHESIS_REVIEWER SUCCEEDED: 「일정만 늘리면 해결된다」를 원문과 대조했습니다 // 제안된 설명마다 결정이 있어야 검토가 끝납니다. // null",
        "HYPOTHESIS_REVIEW RUNNING: 「일정만 늘리면 해결된다」를 원문과 대조하고 있습니다 // 제안된 설명마다 결정이 있어야 검토가 끝납니다. 「일정만 늘리면 해결된다」는 봤고, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」 2개는 아직입니다. 빠진 결정을 채우겠습니다. // 「일정만 늘리면 해결된다」는 봤고, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」 2개는 아직입니다. 빠진 결정을 채우겠습니다.",
        "HYPOTHESIS_REVIEW SUCCEEDED: 「일정만 늘리면 해결된다」를 원문과 대조했습니다 // 제안된 설명마다 결정이 있어야 검토가 끝납니다. 「일정만 늘리면 해결된다」는 봤고, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」 2개는 아직입니다. 빠진 결정을 채우겠습니다. // 「일정만 늘리면 해결된다」는 봤고, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」 2개는 아직입니다. 빠진 결정을 채우겠습니다.",
        "ACTION_PLANNER RUNNING: Psyche 표 3 발사 준비 조건을 확인하려면 다음에 할 작업을 고르고 있습니다 // 실행 전 초안입니다. // null",
        "ACTION_PLANNER SUCCEEDED: Psyche 표 3 발사 준비 조건을 확인하려면 다음에 할 작업을 골랐습니다 // 실행 전 초안입니다. // null",
        "ACTION_COMPARISON RUNNING: Psyche 표 3 발사 준비 조건을 확인하려면 다음에 할 작업을 고르고 있습니다 // 실행 전 초안입니다. // null",
        "ACTION_COMPARISON SUCCEEDED: Psyche 표 3 발사 준비 조건을 확인하려면 다음에 할 작업을 골랐습니다 // 실행 전 초안입니다. // null",
        "EVIDENCE_EXTRACTOR RUNNING: p.6 70.6, p.8 GNC 위치를 확인하고 있습니다 // 실제로 집어넣을 칸과 문장을 찾습니다. // null",
        "EVIDENCE_EXTRACTOR SUCCEEDED: p.6 70.6, p.8 GNC 구절을 골랐습니다 // 실제로 집어넣을 칸과 문장을 찾습니다. // null",
        "EVIDENCE_FOCUS RUNNING: p.6 70.6, p.8 GNC 위치를 확인하고 있습니다 // 실제로 집어넣을 칸과 문장을 찾습니다. p.6 70.6, p.8 GNC 2곳을 확인했습니다 · 전체 5곳 중 일부. // p.6 70.6, p.8 GNC 2곳을 확인했습니다 · 전체 5곳 중 일부.",
        "EVIDENCE_FOCUS SUCCEEDED: p.6 70.6, p.8 GNC 구절을 골랐습니다 // 실제로 집어넣을 칸과 문장을 찾습니다. p.6 70.6, p.8 GNC 2곳을 확인했습니다 · 전체 5곳 중 일부. // p.6 70.6, p.8 GNC 2곳을 확인했습니다 · 전체 5곳 중 일부.",
        "SUFFICIENCY_EXPLAINER RUNNING: Psyche 표 3 발사 준비 조건을 지금 근거로 답할 수 있는지 설명하고 있습니다 // 부족한 항목은 보류 이유로 남깁니다. // null",
        "SUFFICIENCY_EXPLAINER SUCCEEDED: Psyche 표 3 발사 준비 조건을 지금 근거로 답할 수 있는지 설명했습니다 // 부족한 항목은 보류 이유로 남깁니다. // null",
        "COUNTEREVIDENCE_CHALLENGER RUNNING: 「일정만 늘리면 해결된다」, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」을 뒤집을 근거를 찾고 있습니다 // 지지 문장만 보지 않습니다. // null",
        "COUNTEREVIDENCE_CHALLENGER SUCCEEDED: 「일정만 늘리면 해결된다」, 「GNC 소프트웨어만 닫으면 된다」, 「인력 공백」을 뒤집을 근거를 찾았습니다 // 지지 문장만 보지 않습니다. // null",
        "USER_EXPLAINER RUNNING: Psyche 표 3 발사 준비 조건을 사람이 읽게 정리하고 있습니다 // 내부 코드명이 아니라 질문 기준으로 설명합니다. // null",
        "USER_EXPLAINER SUCCEEDED: Psyche 표 3 발사 준비 조건을 사람이 읽게 정리했습니다 // 내부 코드명이 아니라 질문 기준으로 설명합니다. // null",
        "REFERENCE_MAPPER RUNNING: p.6 70.6, p.8 GNC 인용을 원문 위치에 맞추고 있습니다 // 표 번호와 셀 위치를 자료에 연결합니다. // null",
        "REFERENCE_MAPPER SUCCEEDED: p.6 70.6, p.8 GNC 인용을 원문 위치에 맞춰 두었습니다 // 표 번호와 셀 위치를 자료에 연결합니다. // null",
        "CONNECTED_SOURCES RUNNING: psyche-irb-2022.pdf, gao-23-106021.pdf만 열고 있습니다 // 웹 검색을 시작했다는 뜻이 아닙니다. psyche-irb-2022.pdf, gao-23-106021.pdf 목록을 여는 중이며, 답을 쓰기 전입니다. // psyche-irb-2022.pdf, gao-23-106021.pdf 목록을 여는 중이며, 답을 쓰기 전입니다.",
        "CONNECTED_SOURCES SUCCEEDED: psyche-irb-2022.pdf, gao-23-106021.pdf만 열고 있습니다 // 웹 검색을 시작했다는 뜻이 아닙니다. psyche-irb-2022.pdf, gao-23-106021.pdf 목록을 여는 중이며, 답을 쓰기 전입니다. // psyche-irb-2022.pdf, gao-23-106021.pdf 목록을 여는 중이며, 답을 쓰기 전입니다.",
        "SOURCE_SHORTLIST RUNNING: psyche-irb-2022.pdf, gao-23-106021.pdf에서 질문과 맞는 원문 구간을 고르고 있습니다 // 시간 미확인 자료는 확정 근거가 아니라 후보입니다. psyche-irb-2022.pdf, gao-23-106021.pdf에서 원문 구간을 고르는 중이며, 답을 쓰기 전입니다. // psyche-irb-2022.pdf, gao-23-106021.pdf에서 원문 구간을 고르는 중이며, 답을 쓰기 전입니다.",
        "SOURCE_SHORTLIST SUCCEEDED: psyche-irb-2022.pdf, gao-23-106021.pdf에서 질문과 맞는 원문 구간을 골랐습니다 // 시간 미확인 자료는 확정 근거가 아니라 후보입니다. psyche-irb-2022.pdf, gao-23-106021.pdf에서 원문 구간을 고르는 중이며, 답을 쓰기 전입니다. // psyche-irb-2022.pdf, gao-23-106021.pdf에서 원문 구간을 고르는 중이며, 답을 쓰기 전입니다.",
        "HOLD RUNNING: 모델 응답을 기다리다 멈췄습니다 // 연결 자료는 그대로 있고, 답을 확정하지 않았습니다. 답을 쓰기 전에 모델 호출이 멈췄습니다. // 답을 쓰기 전에 모델 호출이 멈췄습니다.",
        "HOLD SUCCEEDED: 모델 호출이 끝나 연구를 멈췄습니다 // 연결 자료는 그대로 있고, 답을 확정하지 않았습니다. 답을 쓰기 전에 모델 호출이 멈췄습니다. // 답을 쓰기 전에 모델 호출이 멈췄습니다.",
        "SOMETHING_ELSE RUNNING: Psyche 표 3 발사 준비 조건을 받아 자료와 조건을 확인하고 있습니다 // 서버가 준 단계 이름만으로는 더 구체적인 작업을 특정하지 못했습니다. // null",
        "SOMETHING_ELSE SUCCEEDED: Psyche 표 3 발사 준비 조건 관련 단계를 마쳤습니다 // 서버가 준 단계 이름만으로는 더 구체적인 작업을 특정하지 못했습니다. // null",
      ]
    `);
  });

  it("pins the sentences when the question carries almost no facts", () => {
    const lines: string[] = [];
    for (const key of KEYS) {
      const live = liveResearchProgress(inPhase(key, { problem: "q" }), []);
      lines.push(`${key}: ${live?.headline} // ${live?.current.detail} // ${live?.missing}`);
    }
    expect(lines).toMatchInlineSnapshot(`
      [
        "RESEARCH_PLANNER: 질문에서 꼭 지킬 조건을 고정하고 있습니다 // 아직 답을 쓰지 않았습니다. // null",
        "REQUIREMENTS: 질문에서 꼭 지킬 조건을 고정하고 있습니다 // 아직 답을 쓰지 않았습니다. // null",
        "EVIDENCE_RERANKER: 질문과 관련된 문장을 다시 줄 세우고 있습니다 // 순위만 매겼고, 지지 여부는 아직 판단하지 않았습니다. // null",
        "SEMANTIC_REVIEWER: 질문 조건을 원문과 대조하고 있습니다 // 관련 있다고 해서 지지하는 것은 아닙니다. // null",
        "EVIDENCE_REVIEW: 질문 조건을 원문과 대조하고 있습니다 // 관련 있다고 해서 지지하는 것은 아닙니다. // null",
        "REVIEW_ADJUDICATOR: 조건 중 빠진 항목과 충돌을 판정하고 있습니다 // 애매한 항목은 보류로 남깁니다. // null",
        "SOURCE_PLANNER: 허용된 사이트에서 더 열 주소를 고르고 있습니다 // 인터넷 전체를 검색하지 않습니다. // null",
        "HYPOTHESIS_GENERATOR: 질문에 대한 가능한 설명을 준비하고 있습니다 // 아직 채택이 아닙니다. // null",
        "HYPOTHESES: 질문에 대한 가능한 설명을 준비하고 있습니다 // 아직 채택이 아닙니다. // null",
        "HYPOTHESIS_REVIEWER: 방금 만든 설명을 원문과 대조하고 있습니다 // 제안된 설명마다 결정이 있어야 검토가 끝납니다. // null",
        "HYPOTHESIS_REVIEW: 방금 만든 설명을 원문과 대조하고 있습니다 // 제안된 설명마다 결정이 있어야 검토가 끝납니다. // null",
        "ACTION_PLANNER: 다음에 할 확인 작업을 고르고 있습니다 // 실행 전 초안입니다. // null",
        "ACTION_COMPARISON: 다음에 할 확인 작업을 고르고 있습니다 // 실행 전 초안입니다. // null",
        "EVIDENCE_EXTRACTOR: 표와 원문 위치를 확인하고 있습니다 // 실제로 집어넣을 칸과 문장을 찾습니다. // null",
        "EVIDENCE_FOCUS: 표와 원문 위치를 확인하고 있습니다 // 실제로 집어넣을 칸과 문장을 찾습니다. 질문에 집어넣을 표·문장이 아직 0곳입니다. 붙인 로컬 원문이 없어 웹 검색은 이 단계에서 시작하지 않습니다. // 질문에 집어넣을 표·문장이 아직 0곳입니다. 붙인 로컬 원문이 없어 웹 검색은 이 단계에서 시작하지 않습니다.",
        "SUFFICIENCY_EXPLAINER: 질문을 지금 근거로 답할 수 있는지 설명하고 있습니다 // 부족한 항목은 보류 이유로 남깁니다. // null",
        "COUNTEREVIDENCE_CHALLENGER: 질문을 뒤집을 근거를 찾고 있습니다 // 지지 문장만 보지 않습니다. // null",
        "USER_EXPLAINER: 현재 판단을 사람이 읽게 정리하고 있습니다 // 내부 코드명이 아니라 질문 기준으로 설명합니다. // null",
        "REFERENCE_MAPPER: 인용과 원문 위치를 맞추고 있습니다 // 표 번호와 셀 위치를 자료에 연결합니다. // null",
        "CONNECTED_SOURCES: 이 프로젝트에 허용된 자료만 열고 있습니다 // 웹 검색을 시작했다는 뜻이 아닙니다. 연결 자료 목록을 여는 중이며, 답을 쓰기 전입니다. // 연결 자료 목록을 여는 중이며, 답을 쓰기 전입니다.",
        "SOURCE_SHORTLIST: 연결 자료에서 질문과 맞는 원문 구간을 고르고 있습니다 // 시간 미확인 자료는 확정 근거가 아니라 후보입니다. 연결 자료에서 원문 구간을 고르는 중이며, 답을 쓰기 전입니다. // 연결 자료에서 원문 구간을 고르는 중이며, 답을 쓰기 전입니다.",
        "HOLD: 모델 응답을 기다리다 멈췄습니다 // 연결 자료는 그대로 있고, 답을 확정하지 않았습니다. 답을 쓰기 전에 모델 호출이 멈췄습니다. // 답을 쓰기 전에 모델 호출이 멈췄습니다.",
        "SOMETHING_ELSE: 요청을 받아 자료와 조건을 확인하고 있습니다 // 서버가 준 단계 이름만으로는 더 구체적인 작업을 특정하지 못했습니다. // null",
      ]
    `);
  });

  it("chooses the Korean particle from the last letter of the subject", () => {
    const lines = ["사과", "책", "ABC", "표 3", "저장소.", "일정?"].map((problem) => {
      const live = liveResearchProgress(inPhase("RESEARCH_PLANNER", { problem }));
      return `${problem}: ${live?.headline}`;
    });
    const withLocators = ["책", "사과", "A3", "표 3", "ABC"].map((label) => {
      const draft = { evidence_focus: { locators: [{ exact_text: label }] } };
      const reranked = liveResearchProgress(inPhase("EVIDENCE_RERANKER", {}, draft));
      const reviewed = liveResearchProgress(inPhase("SEMANTIC_REVIEWER", { problem: "발사 조건" }, draft));
      return `${label}: ${reranked?.headline} | ${reviewed?.headline}`;
    });
    expect([...lines, ...withLocators]).toMatchInlineSnapshot(`
      [
        "사과: 사과를 고정하고 있습니다",
        "책: 질문에서 꼭 지킬 조건을 고정하고 있습니다",
        "ABC: ABC를 고정하고 있습니다",
        "표 3: 표 3을 고정하고 있습니다",
        "저장소.: 저장소를 고정하고 있습니다",
        "일정?: 일정을 고정하고 있습니다",
        "책: 책과 관련된 문장을 다시 줄 세우고 있습니다 | 발사 조건이 책 원문에 있는지 대조하고 있습니다",
        "사과: 사과와 관련된 문장을 다시 줄 세우고 있습니다 | 발사 조건이 사과 원문에 있는지 대조하고 있습니다",
        "A3: A3과 관련된 문장을 다시 줄 세우고 있습니다 | 발사 조건이 A3 원문에 있는지 대조하고 있습니다",
        "표 3: 표 3과 관련된 문장을 다시 줄 세우고 있습니다 | 발사 조건이 표 3 원문에 있는지 대조하고 있습니다",
        "ABC: ABC와 관련된 문장을 다시 줄 세우고 있습니다 | 발사 조건이 ABC 원문에 있는지 대조하고 있습니다",
      ]
    `);
  });

  it("clips, deduplicates and limits the quoted hypotheses and the joined lists", () => {
    const long = "아주 긴 가설 문장은 스물여덟 글자를 넘기면 말줄임표로 잘려야 합니다 그래서 길게 적습니다";
    const draft = {
      portfolio: {
        hypotheses: [
          { hypothesis_id: "h1", statement: long },
          { hypothesis_id: "h2", statement: "같은   문장" },
          { hypothesis_id: "h3", statement: "같은 문장" },
          { hypothesis_id: "h4", statement: "넷째 설명" },
          { hypothesis_id: "h5", statement: "다섯째 설명" },
        ],
      },
    };
    const generated = liveResearchProgress(inPhase("HYPOTHESIS_GENERATOR", {}, draft));
    const reviewed = liveResearchProgress(inPhase("HYPOTHESIS_REVIEW", {}, draft));
    expect([generated?.headline, reviewed?.headline, reviewed?.missing]).toMatchInlineSnapshot(`
      [
        "「아주 긴 가설 문장은 스물여덟 글자를 넘기면 말줄…」, 「같은 문장」, 「넷째 설명」을 준비하고 있습니다",
        "「아주 긴 가설 문장은 스물여덟 글자를 넘기면 말줄…」, 「같은 문장」, 「넷째 설명」을 원문과 대조하고 있습니다",
        "「아주 긴 가설 문장은 스물여덟 글자를 넘기면 말줄…」, 「같은 문장」부터 대조하겠습니다. 빠진 5개 결정을 채우기 전입니다.",
      ]
    `);
  });
});

describe("one-line texts", () => {
  it("pins the model, token and wait lines", () => {
    const cases: Record<string, unknown>[] = [
      { request: { operation_id: "o", model_settings: { model: "gpt-x", reasoning_effort: "high" } } },
      { request: { operation_id: "o", model_settings: { model: "gpt-x" } } },
      { request: { operation_id: "o", model_settings: { reasoning_effort: "low" } } },
      { request: { operation_id: "o", model_settings: {} } },
      { request: { operation_id: "o" } },
      { usage: { input_tokens: 1, output_tokens: 2, total_tokens: 12400, state: "COMPLETE", unreported_calls: 0, cumulative_token_limit_enforced: false } },
      { usage: { input_tokens: 1, output_tokens: 2, total_tokens: null, state: "PARTIAL", unreported_calls: 1, cumulative_token_limit_enforced: false, cached_input_tokens: 3000 } },
      { model_dispatches: [{ state: "RESERVED", transport_observation: { elapsed_ms: 47000, received_bytes: 0 } }] },
      { model_dispatches: [{ state: "RESERVED", transport_observation: { elapsed_ms: 47000, first_byte_ms: 1200 } }] },
      { model_dispatches: [{ state: "RESERVED" }] },
      { model_dispatches: [{ state: "OBSERVED", transport_observation: { elapsed_ms: 173, received_bytes: 2048 } }] },
      { model_dispatches: [{ state: "OBSERVED", received_bytes: 5, transport_observation: { elapsed_ms: 90000 } }] },
      { model_dispatches: [{ state: "OBSERVED", transport_observation: { elapsed_ms: 5000 } }] },
      { model_dispatches: [{ state: "OBSERVED" }] },
      { model_dispatches: [{ state: "OBSERVED", received_bytes: 1 }, { state: "RESERVED", transport_observation: { elapsed_ms: 1000 } }] },
    ];
    const lines = cases.map((extra) => {
      const live = liveResearchProgress(status(extra));
      return JSON.stringify([live?.modelLine, live?.usageLine, live?.waitLine]);
    });
    expect(lines).toMatchInlineSnapshot(`
      [
        "["gpt-x · 추론 high",null,null]",
        "["gpt-x",null,null]",
        "["추론 low",null,null]",
        "[null,null,null]",
        "[null,null,null]",
        "[null,"사용 토큰 12,400",null]",
        "[null,"사용 토큰 미확인 (부분 관측) · 캐시 입력 3,000",null]",
        "[null,null,"모델 응답 대기 · 47초 · 첫 글자 아직 없음"]",
        "[null,null,"모델 응답 대기 · 47초 · 첫 글자 1초"]",
        "[null,null,"모델 응답 대기 · 경과 미확인 · 첫 글자 아직 없음"]",
        "[null,null,"마지막 모델 응답 2,048B · 173ms"]",
        "[null,null,"마지막 모델 응답 5B · 1분 30초"]",
        "[null,null,"마지막 모델 호출 · 5초"]",
        "[null,null,null]",
        "[null,null,"모델 응답 대기 · 1초 · 첫 글자 아직 없음"]",
      ]
    `);
  });

  it("pins the finished-stage and model-call payload texts, including sizes", () => {
    const stages = [500, 1023, 1024, 1536, 10239, 10240, 1048576, 5242880, 20971520, null, -1].map((context_bytes) => ({
      role: "RESEARCH_PLANNER", elapsed_ms: 2000, context_bytes, dispatch_ids: ["a", "b"],
    }));
    const live = liveResearchProgress(status({ completed_stages: stages as unknown }));
    const published = liveResearchProgress(
      status({
        activity_events: [
          { seq: 1, kind: "action", action_type: "stage_completed", live: false, payload: { elapsed_ms: 1500, context_bytes: 2048, dispatch_count: 2 } },
          { seq: 2, kind: "action", action_type: "source_read", live: false, payload: { page: 3, exact_text: " 표 3 " } },
          { seq: 3, kind: "action", action_type: "source_read", live: false, payload: { page: null, exact_text: null } },
          { seq: 4, kind: "action", action_type: "hypothesis_review", live: false, payload: { hypothesis_count: 4, review_count: 4 } },
          { seq: 5, kind: "action", action_type: "hypothesis_review", live: false, payload: { hypothesis_count: 4, review_count: 1 } },
          { seq: 6, kind: "action", action_type: "hypothesis_review", live: false, payload: { hypothesis_count: 4 } },
          { seq: 7, kind: "action", action_type: "model_call", live: false, payload: { state: "OBSERVED", received_bytes: 1200, elapsed_ms: 800 } },
          { seq: 8, kind: "action", action_type: "model_call", live: true, payload: { state: "RESERVED", elapsed_ms: 3000, first_byte_ms: 400 } },
        ],
      }),
    );
    const flat = (events: { kind: string; text: string; payload?: string | null; live?: boolean }[] | undefined) =>
      events?.map((event) => `${event.kind}|${event.text}|${event.payload ?? ""}|${event.live ?? false}`);
    expect({ stages: flat(live?.events), published: flat(published?.events) }).toMatchInlineSnapshot(`
      {
        "published": [
          "action|단계 완료|2초 · 맥락 2.0KB · 모델 2회|false",
          "action|원문 위치|p.3 · 표 3|false",
          "action|원문 위치||false",
          "action|가설 검토|가설 4개 · 검토 4개|false",
          "action|가설 검토|가설 4개 중 1개만 결정|false",
          "action|가설 검토|가설 4개 · 결정은 아직 없습니다|false",
          "action|모델 호출|1,200B · 800ms|false",
          "action|모델 호출|3초 · 첫 글자 400ms|true",
          "note|표 3 발사 준비 조건을 고정하고 있습니다||true",
        ],
        "stages": [
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 맥락 500B · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 맥락 1,023B · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 맥락 1.0KB · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 맥락 1.5KB · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 맥락 10.0KB · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 맥락 10KB · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 맥락 1.0MB · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 맥락 5.0MB · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 맥락 20MB · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정했습니다||false",
          "action|단계 완료|2초 · 모델 2회|false",
          "note|표 3 발사 준비 조건을 고정하고 있습니다||true",
        ],
      }
    `);
  });

  it("pins the composer line for each execution state", () => {
    const lines = [
      composerActivity(status({ execution_state: "PAUSE_PENDING" })),
      composerActivity(status({ execution_state: "PAUSED" })),
      composerActivity(
        status({ attempt: { operation_id: "o", phase: "HOLD", status: "RUNNING", external_effect_state: "DISPATCHING", remote_observation: "UNKNOWN" } }),
      ),
      composerActivity(status({ operation_state: "SUCCEEDED" })),
      composerActivity(status({ attempt: undefined })),
      composerActivity(inPhase("EVIDENCE_FOCUS", { model_dispatches: [{ state: "RESERVED", transport_observation: { elapsed_ms: 47000 } }] })),
    ];
    expect(lines).toMatchInlineSnapshot(`
      [
        "현재 단계를 마치고 멈추는 중입니다.",
        "작업이 일시정지되어 있습니다.",
        "외부 실행의 종료 여부를 아직 확인하지 못했습니다.",
        null,
        null,
        "Psyche 표 3 발사 준비 조건 위치를 확인하고 있습니다 · 모델 응답 대기 · 47초 · 첫 글자 아직 없음",
      ]
    `);
  });

  it("pins the hold reasons and the evidence labels", () => {
    const reasons = ["HTTP 429", "AUTH_REQUIRED", "x403", "MODEL_NOT_SUPPORTED", "400", "other", ""].map((terminal_reason) => {
      const live = liveResearchProgress(
        inPhase("HOLD", {
          operation_state: "SUCCEEDED",
          current_result: { operation_id: "o", phase: "HOLD", state: "PARTIAL", completion: "TERMINAL", terminal_reason, basis_digest: "0", result: {}, gaps: [], next_steps: [], source_refs: [], record_refs: [] },
        }),
      );
      return `${terminal_reason}: ${live?.missing}`;
    });
    expect({ reasons, labels: evidenceLabels(inPhase("EVIDENCE_FOCUS", {}, RICH_DRAFT)) }).toMatchInlineSnapshot(`
      {
        "labels": [
          "p.6 70.6",
          "p.8 GNC",
        ],
        "reasons": [
          "HTTP 429: 계정 사용 한도로 모델 호출이 거절됐습니다.",
          "AUTH_REQUIRED: 모델 계정 인증이 거절됐습니다.",
          "x403: 모델 계정 인증이 거절됐습니다.",
          "MODEL_NOT_SUPPORTED: 이 ChatGPT Codex 계정에서 지원하지 않는 모델입니다.",
          "400: 모델이 요청을 거절했습니다.",
          "other: 답을 쓰기 전에 모델 호출이 멈췄습니다.",
          ": 답을 쓰기 전에 모델 호출이 멈췄습니다.",
        ],
      }
    `);
  });
});