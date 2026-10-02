/** Plain-Korean labels for values the server records. Unknown values are shown as recorded. */

const labels: Record<string, string> = {
  // evidence authority
  UNCLASSIFIED: "미분류", INFORMAL: "비공식 참고", OFFICIAL: "공식", APPROVED: "승인본",
  SUPERSEDED: "대체됨", NOT_ADMISSIBLE: "사용 불가",
  // source time
  ELIGIBLE: "기준시점에 적합", AFTER_CUTOFF: "기준시점 이후 자료", UNKNOWN_TIME: "시점 미확인", PROHIBITED_CONTEXT: "사용 금지 맥락",
  // evidence support
  DISCOVERED: "발견됨", EXTRACTED: "원문 추출됨", SUPPORTED_CANDIDATE: "뒷받침 후보", SUPPORTED: "뒷받침됨",
  CONTRADICTED: "충돌함", REFUTED: "반박됨", UNRESOLVED: "미해결",
  // evaluation criteria
  SATISFIED: "충족", NOT_APPLICABLE: "해당 없음", NOT_ASSESSED: "미평가", APPLIED: "판단 반영됨",
  // risk and execution authority
  R0: "위험 낮음 · R0", R1: "낮음 · R1", R2: "중간 · R2", R3: "높음 · R3", R4: "금지 수준 · R4",
  AUTO_R0: "자동 실행 가능", PREAUTHORIZED_R1: "사전 허용 범위", SANDBOX_ONLY_R2: "격리 실행만",
  HUMAN_REQUIRED_R3: "사람 승인 필요", PROHIBITED_R4: "실행 금지",
  // reversibility
  FULL: "되돌릴 수 있음",
  // action state (APPROVED and PARTIAL mean something else elsewhere; see actionStateLabel)
  PROPOSED: "제안됨", RISK_CLASSIFIED: "위험 분류됨", AUTO_ALLOWED: "자동 실행 허용", APPROVAL_PENDING: "승인 대기",
  EXECUTING: "실행 중", INVALIDATED: "무효화됨", REJECTED: "거절됨", PROHIBITED: "금지됨",
  // memory
  FACT: "사실", FAILURE: "실패 기록", REFERENCE: "참고값", HYPOTHESIS: "가설", DECISION: "판단", ACTION: "행동", LESSON: "교훈",
  EXCLUDED: "회상 제외", AUDIT_ONLY: "기록 확인용", EVIDENCE_SEARCH_ONLY: "근거 검색에만 사용",
  WORKING_CONTEXT: "작업 맥락에 사용", ACTION_CONTEXT: "행동 계획에 사용",
  CURRENT: "현재", RETIRED: "폐기됨",
};

const counterReview: Record<string, string> = {
  SUPPORTED: "반대 근거를 찾았고 가설을 뒷받침하는 쪽으로 확인됨",
  ELIMINATED_WITHIN_SCOPE: "이 범위에서는 반박됨",
  UNRESOLVED_NO_RESULTS: "반대 근거 탐색에서 결과가 없어 미해결",
  UNRESOLVED_INDEPENDENCE: "찾은 근거가 독립적이지 않아 미해결",
  UNRESOLVED_AUTHORITY: "찾은 근거의 권위를 확인하지 못해 미해결",
  UNRESOLVED_TEMPORAL: "찾은 근거의 시점이 맞지 않아 미해결",
  UNRESOLVED_PROHIBITED_CONTEXT: "사용이 금지된 맥락이라 미해결",
  UNRESOLVED_POLICY_BLOCKED: "정책상 탐색이 막혀 미해결",
  UNRESOLVED_FAILED: "탐색이 실패해 미해결",
  UNRESOLVED_CONFLICT: "찾은 근거끼리 충돌해 미해결",
};

const answerStatus: Record<string, string> = {
  PARTIAL_HOLD: "판단 보류",
  ASSESSED_WITH_OPEN_CHECKS: "부분 답변",
  ASSESSED_FOR_REQUEST: "답변 완료",
};

const providers: Record<string, string> = {
  default: "기본 연결", "codex-oauth": "ChatGPT 계정", "xai-oauth": "xAI 계정", "claude-oauth": "Claude 계정",
  "claude-code": "Claude Code 계정", openai: "OpenAI API 키", anthropic: "Anthropic API 키", xai: "xAI API 키",
};

const eyebrows: Record<string, string> = {
  EVIDENCE: "근거", "AUTHORIZED SOURCES": "허용된 자료", "RESEARCH HISTORY": "연구 이력",
  "RESEARCH WORKSPACE": "연구 작업 공간", "LOCAL WORKSPACE": "이 컴퓨터", "REVIEW WORKSPACE": "심사 작업 공간",
};

export function statusLabel(value: string | null | undefined): string | null {
  return typeof value === "string" ? labels[value] ?? null : null;
}
const reversibility: Record<string, string> = { FULL: "되돌릴 수 있음", PARTIAL: "일부만 되돌릴 수 있음", NONE: "되돌릴 수 없음" };
const actionState: Record<string, string> = { APPROVED: "승인됨", PARTIAL: "일부만 실행됨", SUCCEEDED: "완료", FAILED: "실패", CANCELLED: "취소됨" };
export function reversibilityLabel(value: string | null | undefined): string | null {
  return typeof value === "string" ? reversibility[value] ?? value : null;
}
export function actionStateLabel(value: string | null | undefined): string | null {
  return typeof value === "string" ? actionState[value] ?? statusLabel(value) ?? value : null;
}
/** Terms inside a criteria row (target, relation, validation, blocker). */
const coverageTerms: Record<string, string> = {
  RESEARCH_GAP: "조사 공백", APPLIED: "판단 반영됨", NOT_ASSESSED: "미평가", INCONCLUSIVE: "결론을 내지 못함",
  QUALIFIES: "조건부로 해당", SUPPORTS: "뒷받침", CONTRADICTS: "충돌", "answer:HOLD": "답변을 보류시킨 요인",
  APPLICABLE: "적용됨", COVERAGE_UNAVAILABLE: "평가기준 정보 없음", ASSESSMENT_UNAVAILABLE: "평가 결과 없음",
};
export function coverageTermLabel(value: string): string { return coverageTerms[value] ?? value; }
const actionFamilies: Record<string, string> = {
  READ_ONLY_ANALYSIS: "읽기 전용 분석", EVIDENCE_REQUEST: "자료 요청", SANDBOX_REPLAY: "격리 환경 재현",
  CONFIGURATION_CHANGE: "설정 변경", CONTROLLED_RERUN: "통제된 재실행", OFFICIAL_CRITERION_CHANGE: "공식 기준 변경",
};
export function actionFamilyLabel(value: string): string | null { return actionFamilies[value] ?? null; }
const memoryExclusions: Record<string, string> = {
  PROJECT_MISMATCH: "다른 프로젝트의 기억", RESOURCE_ACCESS_DENIED: "접근 권한이 없음", AMBIGUOUS_OR_CONFLICTING: "근거가 모호하거나 충돌함",
  AUTHORITY_INVALID: "출처 권위가 확인되지 않음", CUTOFF_INVALID: "기준시점이 맞지 않음", OWNER_REVISION_NOT_CURRENT: "원본이 바뀌어 다시 확인이 필요함",
  DEPENDENCY_REVIEW_REQUIRED: "근거가 바뀌어 재검토가 필요함", RECALL_INELIGIBLE: "다시 불러오지 않는 기억", ACTION_INELIGIBLE: "행동 계획에는 쓰지 않는 기억",
  SCOPE_MISMATCH: "이 작업 범위와 맞지 않음", QUERY_IRRELEVANT: "이번 질문과 관련이 낮음", NOT_CURRENT: "현재 버전이 아님",
  RECALL_CLASS_NOT_ACTIVE: "지금 쓰지 않는 종류", BUDGET: "분량 제한으로 제외",
  CONTAINER_NOT_RECALLED: "여러 기억을 묶은 기록이라 낱개 기억으로 불러옴", OMITTED_BY_BUDGET: "분량 제한으로 제외",
  MEMORY_INJECTION_OFF: "이 프로젝트가 기억 사용을 꺼 둠",
  AUTO_MEMORY_WEAK_MATCH: "자동 기억이라 이번 질문과 약하게만 겹쳐 넣지 않음",
  AUTO_MEMORY_NO_EVIDENCE: "근거 자료가 없는 자동 기억",
  SUPERSEDED_BY_NEWER_VERSION: "더 새로운 버전이 있음", TRANSITION_HOLD: "검토에서 보류됨", TRANSITION_QUARANTINE: "검토에서 격리됨",
  TRANSITION_REVISE: "검토에서 수정 필요로 판정됨",
};
export const memoryStateLabels = {
  transition: (value: string) => ({ COMMIT: "반영됨", REVISE: "수정 필요", HOLD: "보류", QUARANTINE: "격리" } as Record<string, string>)[value] ?? value,
  support: (value: string) => value === "SUPPORTED" ? "근거 충분" : "근거 확인 필요",
  authority: (value: string) => value === "AUTHORITATIVE" ? "공식 출처" : "출처 권위 확인 필요",
};
export function memoryExclusionLabel(value: string): string { return memoryExclusions[value] ?? value; }
const effortBands: Record<string, Record<string, string>> = {
  TIME: { LOW: "짧음", MEDIUM: "보통", HIGH: "김", UNKNOWN: "미확인" },
  COST_EFFORT: { LOW: "낮음", MEDIUM: "중간", HIGH: "높음", UNKNOWN: "미확인" },
};
export function effortDimensionLabel(value: string): string { return value === "TIME" ? "시간" : value === "COST_EFFORT" ? "비용·품" : value; }
export function effortBandLabel(dimension: string, band: string): string { return effortBands[dimension]?.[band] ?? band; }
export function effortBandOptions(dimension: string): { value: string; label: string }[] {
  return ["LOW", "MEDIUM", "HIGH", "UNKNOWN"].map(value => ({ value, label: effortBandLabel(dimension, value) }));
}
export function estimatorLabel(value: string): string { return value === "AI" ? "AI 추정" : value === "HUMAN" ? "사람 추정" : value === "RULE" ? "규칙 추정" : value; }
/** Korean wording for the next step, chosen by action type; the server's own (English) label stays out of the visible text. */
export function nextActionLabel(action: { action_type: string; reason_codes?: string[] }): string {
  switch (action.action_type) {
    case "REVIEW_GAPS": return "보류된 기준의 부족한 자료 확인";
    case "REVIEW_CURRENTNESS": return "이 답변을 쓰기 전에 근거가 현재 기준인지 확인";
    case "OPEN_RESULT_DETAIL": return action.reason_codes?.includes("RESULT_NOT_PRODUCED") ? "현재 조사 시도의 진행 상태 확인" : "실행 결과 확인";
    case "START_FOLLOWUP": return "후속 질문으로 이어가기";
    case "NONE": return "기록된 다음 행동 없음";
    default: return "이 화면에서 안내할 수 없는 다음 행동";
  }
}
export function counterReviewLabel(value: string): string { return counterReview[value] ?? value; }
export function answerStatusLabel(value: string | null | undefined): string | null {
  return typeof value === "string" ? answerStatus[value] ?? null : null;
}
export function providerLabel(value: string): string { return providers[value] ?? value; }
export function eyebrowLabel(value: string): string { return eyebrows[value] ?? value; }
