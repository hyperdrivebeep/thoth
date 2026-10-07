/** Plain-language text for the reason codes the research-history screens receive. Raw codes stay in the technical details. */

const reasons: Record<string, string> = {
  // Currentness of an answer (research_freshness.py)
  HEAD_UNAVAILABLE: "현재 기록의 최신 상태를 읽을 수 없습니다.",
  DEPENDENCY_INVALIDATED: "이 답변이 기대는 다른 기록이 더 이상 유효하지 않습니다.",
  DEPENDENCY_REVIEW_REQUIRED: "이 답변이 기대는 다른 기록을 다시 검토해야 합니다.",
  LEGACY_BASIS_UNKNOWN: "옛 기록이라 어떤 자료 기준으로 만든 답변인지 확인할 수 없습니다.",
  RESULT_OPERATION_UNAVAILABLE: "이 답변을 만든 실행 기록을 찾을 수 없습니다.",
  ATTEMPT_ENDED_WITH_CHECKPOINT: "조사가 끝까지 가지 못하고 중간 저장 상태에서 멈췄습니다.",
  REQUEST_BASIS_CHANGED: "질문을 한 뒤 질문의 기준이 바뀌었습니다.",
  POLICY_OR_CUTOFF_CHANGED: "프로젝트의 정책이나 기준시점이 바뀌었습니다.",
  SOURCE_BASIS_CHANGED: "답변이 쓴 자료가 이후 바뀌었습니다.",
  MEMORY_CORRECTED_AFTER_RESULT: "이 결과가 쓴 기억이 이후 정정되었습니다.",
  BASIS_INCOMPLETE: "답변의 자료 기준 기록이 불완전합니다.",
  OWNER_UNAVAILABLE: "이 기록을 가진 항목을 읽을 수 없습니다.",
  REQUEST_SOURCE_OR_ACCESS_BASIS_CHANGED: "질문 뒤에 자료나 접근 범위가 바뀌었습니다.",
  UNKNOWN_BASIS: "현재 자료 기준을 확인할 수 없습니다.",
  RESULT_SUPERSEDED: "더 새로운 답변이 이 답변을 대체했습니다.",
  // Coverage of what the question needs (research_coverage.py)
  MANDATORY_RULE_COVERAGE_MISSING: "반드시 확인해야 하는 규칙 중 아직 다루지 못한 것이 있습니다.",
  PROFILE_DECISION_REQUIRED: "적용할 기준을 사용자가 먼저 정해야 합니다.",
  REQUIREMENT_INTERPRETATION_INCOMPLETE: "질문이 요구하는 것을 아직 다 풀어내지 못했습니다.",
  REQUIREMENT_GAPS: "아직 충족하지 못한 요구 항목이 있습니다.",
  GAP_MAPPING_INCOMPLETE: "빠진 부분이 어느 요구 항목에 해당하는지 아직 연결하지 못했습니다.",
  REQUIRED_CONTEXT_OMITTED: "답하는 데 꼭 필요한 맥락이 빠져 있습니다.",
  REQUIRED_SCOPE_UNEXAMINED: "꼭 살펴봐야 하는 범위를 아직 살펴보지 못했습니다.",
  EVIDENCE_CONFLICT: "근거끼리 서로 맞지 않는 부분이 있습니다.",
  REVIEWED_CONFLICT_HISTORY: "근거가 충돌했던 기록이 있고 검토를 거쳤습니다.",
  EXPLICIT_PUBLIC_SEARCH: "사용자가 공개 웹 검색을 직접 요청했습니다.",
  // Review list and comparison projections (research_followup_projection.py, research_followup.py)
  ASSESSMENT_UNAVAILABLE: "이 항목의 평가 기록을 읽을 수 없습니다.",
  COVERAGE_UNAVAILABLE: "근거 충족 여부 기록을 읽을 수 없습니다.",
  REQUIREMENT_SET_UNAVAILABLE: "요구 항목 기록을 읽을 수 없습니다.",
  RESULT_NOT_PRODUCED: "아직 답변이 만들어지지 않았습니다.",
  RESULT_COMPARE_REQUIRES_TWO_IDENTITIES: "비교하려면 서로 다른 두 답변이 필요합니다.",
  FAILED: "조사가 실패로 끝났습니다.",
  CANCELLED: "조사가 취소되었습니다.",
  // How an attempt ended (research_attempt_runner.py, research_failure.py)
  BOUNDED_RESEARCH_COMPLETE: "정해진 범위의 조사를 마쳤습니다.",
  RESEARCH_INTERNAL_ERROR: "조사 중 예상하지 못한 내부 오류가 있었습니다.",
};

const CODE_SHAPE = /^[A-Z][A-Z0-9_]+$/;

/** A sentence for one recorded reason. Text that is not a code (a gap or next step the research wrote) is shown as it is. */
export function describeHistoryReason(code: string): string {
  if (reasons[code]) return reasons[code];
  return CODE_SHAPE.test(code) ? "기록된 다른 이유가 있습니다." : code;
}

/** Sentences for a list of reasons, each wording once, in the order given. */
export function describeHistoryReasons(codes: readonly string[]): string[] {
  return [...new Set(codes.map(describeHistoryReason))];
}

export const isKnownHistoryReason = (code: string) => code in reasons;
