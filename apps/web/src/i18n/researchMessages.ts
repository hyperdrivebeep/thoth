export const researchMessages = {
  "timeline.title": {
    "ko": "연구 대화",
    "en": "Research conversation"
  },
  "timeline.accessUnverified": {
    "ko": "대화의 접근 권한이나 저장 기준을 확인하지 못해 답변을 표시하지 않습니다.",
    "en": "Answers are hidden because conversation access or the saved basis could not be verified."
  },
  "timeline.loadFailed": {
    "ko": "대화를 불러오지 못했습니다.",
    "en": "The conversation could not be loaded."
  },
  "timeline.loading": {
    "ko": "대화를 읽는 중…",
    "en": "Loading conversation…"
  },
  "timeline.older": {
    "ko": "이전 대화 불러오기",
    "en": "Load earlier messages"
  },
  "timeline.refreshFailed": {
    "ko": "대화를 다시 확인하지 못했습니다. 마지막으로 불러온 답변을 표시하며, 최신 상태는 확인되지 않았습니다. ",
    "en": "The conversation could not be refreshed. The last loaded answers are shown; their current status is unverified. "
  },
  "timeline.loadFailedSpace": {
    "ko": "대화를 불러오지 못했습니다. ",
    "en": "The conversation could not be loaded. "
  },
  "timeline.limited": {
    "ko": "조회 범위를 넘는 과거 이력이 있습니다. 기록에서 확인할 수 있습니다.",
    "en": "Older history exceeds this view's range. Open research history to see it."
  },
  "timeline.unsupported": {
    "ko": "이 서버는 과거 대화 조회를 지원하지 않습니다. 확인 가능한 현재 기록만 표시합니다.",
    "en": "This server does not support conversation history. Only current records that can be verified are shown."
  },
  "timeline.queued": {
    "ko": "현재 조사가 끝나면 이 지시를 이어서 반영합니다",
    "en": "This instruction will be applied after the current investigation finishes"
  },
  "timeline.unavailable": {
    "ko": "이 작업의 대화 기록을 현재 화면에서 불러올 수 없습니다.",
    "en": "This task's conversation history is unavailable in the current view."
  },
  "timeline.recoverAdvice": {
    "ko": "저장된 연구 이력과 현재 프로젝트 기록을 확인하거나, 같은 작업에서 새 질문을 이어갈 수 있습니다.",
    "en": "Check saved research history and current project records, or ask a new question in this task."
  },
  "timeline.newQuestion": {
    "ko": "새 질문으로 이어가기",
    "en": "Continue with a new question"
  },
  "timeline.openHistory": {
    "ko": "연구 이력 확인",
    "en": "View research history"
  },
  "timeline.emptyTitle": {
    "ko": "지금 무엇이 막혀 있나요?",
    "en": "What are you working through?"
  },
  "timeline.emptyNotice": {
    "ko": "질문과 자료에서 출발해 근거, 가능한 설명, 다음 행동을 함께 확인합니다.",
    "en": "Start with a question and sources to examine evidence, possible explanations, and next actions."
  },
  "result.slow": {
    "ko": "응답이 매우 느려짐",
    "en": "Response stalled"
  },
  "result.runaway": {
    "ko": "응답이 끝나지 않고 계속 생성됨",
    "en": "Response continued without finishing"
  },
  "result.connectionError": {
    "ko": "연결 오류",
    "en": "Connection error"
  },
  "result.deadline": {
    "ko": "호출 시간 초과",
    "en": "Call deadline reached"
  },
  "result.responseTimeout": {
    "ko": "응답 시간 초과",
    "en": "Response timeout"
  },
  "result.cancelled": {
    "ko": "연구 실행이 취소되었습니다. 자동 재실행하지 않습니다.",
    "en": "Research was cancelled. It will not restart automatically."
  },
  "result.cancelUnknown": {
    "ko": "취소 사유는 현재 기록에서 확인되지 않습니다.",
    "en": "The cancellation reason is not available in the current record."
  },
  "result.beforeCancel": {
    "ko": "취소 전까지 확인한 내용",
    "en": "Findings saved before cancellation"
  },
  "result.noSavedAnswer": {
    "ko": "이 요청에 저장된 답변은 없습니다.",
    "en": "This request has no saved answer."
  },
  "result.incomplete": {
    "ko": "연구를 완료하지 못했습니다. 마지막으로 저장된 자료와 질문은 보존돼 있습니다.",
    "en": "Research could not finish. The last saved sources and question are preserved."
  },
  "result.timeLimit": {
    "ko": "모델 응답 시간 제한에 도달했습니다.",
    "en": "The model response time limit was reached."
  },
  "result.researchLimit": {
    "ko": "조사의 총호출 수 또는 총시간 제한에 도달했습니다. 누적 토큰 상한과는 별도 제한입니다.",
    "en": "The research call-count or elapsed-time limit was reached. This is separate from a cumulative token limit."
  },
  "result.quota": {
    "ko": "ChatGPT 사용 한도에 걸려 모델 호출이 거절됐습니다.",
    "en": "The model call was rejected because the ChatGPT usage limit was reached."
  },
  "result.authentication": {
    "ko": "모델 계정 인증이 거절됐습니다. 연결을 다시 확인하세요.",
    "en": "Model account authentication was rejected. Check the connection."
  },
  "result.unsupportedModel": {
    "ko": "이 ChatGPT Codex 계정에서 지원하지 않는 모델입니다. 자동 재실행하지 않습니다.",
    "en": "This model is not supported by this ChatGPT Codex account. It will not restart automatically."
  },
  "result.requestRejected": {
    "ko": "모델이 요청을 거절했습니다. 자동 재실행하지 않습니다.",
    "en": "The model rejected the request. It will not restart automatically."
  },
  "result.storageFailed": {
    "ko": "내부 결과 저장에 실패했습니다.",
    "en": "Saving the research result failed."
  },
  "result.internalFailed": {
    "ko": "내부 연구 처리에 실패했습니다.",
    "en": "Research processing failed."
  },
  "result.remoteUnknown": {
    "ko": "원격 모델의 종료 여부는 확인되지 않았습니다. 자동 재실행하지 않습니다.",
    "en": "It is unknown whether the remote model stopped. It will not restart automatically."
  },
  "result.beforeInterruption": {
    "ko": "중단 전까지 확인한 사실",
    "en": "Facts verified before interruption"
  },
  "result.retryQuestion": {
    "ko": "같은 질문으로 다시 조사",
    "en": "Research the same question again"
  },
  "result.retryNotice": {
    "ko": " 질문을 입력창에 채워 둡니다. 보내기를 눌러야 시작합니다.",
    "en": " The question is placed in the composer. Research starts only when you send it."
  },
  "result.resume": {
    "ko": "이어서 조사",
    "en": "Resume research"
  },
  "result.beforeFailure": {
    "ko": "실패 전까지 확인한 내용",
    "en": "Findings saved before failure"
  },
  "result.stale": {
    "ko": "현재 기준으로 다시 확인이 필요한 과거 답변입니다.",
    "en": "This is a past answer that needs checking against the current basis."
  },
  "result.openChecks": {
    "ko": "아직 확인할 내용",
    "en": "Checks still open"
  },
  "result.alternatives": {
    "ko": "다른 설명과 확인할 점",
    "en": "Alternative explanations and checks"
  },
  "result.noHypotheses": {
    "ko": "현재 자료로 제안된 가설이 없습니다.",
    "en": "No hypotheses were proposed from the current sources."
  },
  "result.nextActions": {
    "ko": "다음에 해볼 일",
    "en": "Possible next actions"
  },
  "result.authorityRequired": {
    "ko": "실행 제안입니다. 대상과 영향을 검토한 권한 결정이 필요합니다.",
    "en": "This is an execution proposal. An authorized decision must review its target and effects."
  },
  "result.details": {
    "ko": "답변 상세",
    "en": "Answer details"
  },
  "result.sources": {
    "ko": "근거 원문",
    "en": "Original evidence"
  },
  "result.compareHypotheses": {
    "ko": "가설 비교",
    "en": "Compare hypotheses"
  },
  "result.compareActions": {
    "ko": "행동 비교",
    "en": "Compare actions"
  },
  "result.changes": {
    "ko": "변경 이력",
    "en": "Change history"
  },
  "result.more": {
    "ko": "다른 상세 보기",
    "en": "More details"
  },
  "result.references": {
    "ko": "참고값·조건",
    "en": "Reference values and conditions"
  },
  "result.map": {
    "ko": "연구 지도",
    "en": "Research map"
  },
  "result.outcome": {
    "ko": "실행 결과",
    "en": "Execution result"
  },
  "result.memory": {
    "ko": "프로젝트 기억",
    "en": "Project memory"
  },
  "result.checking": {
    "ko": "자료와 조건을 확인하고 있습니다. 아직 저장된 답변은 없습니다.",
    "en": "Checking sources and conditions. No answer has been saved yet."
  },
  "result.noLinkedAnswer": {
    "ko": "이 요청에 연결된 저장 답변이 아직 없습니다.",
    "en": "There is no saved answer linked to this request yet."
  },
  "progress.current": {
    "ko": "현재 작업",
    "en": "Current task"
  },
  "progress.pause": {
    "ko": "일시정지",
    "en": "Pause"
  },
  "progress.resume": {
    "ko": "이어서 진행",
    "en": "Continue"
  },
  "progress.stop": {
    "ko": "중단 요청",
    "en": "Request stop"
  },
  "progress.stopConfirm": {
    "ko": "이 작업을 중단할까요? 이미 진행한 외부 실행의 종료 여부는 별도로 확인됩니다.",
    "en": "Stop this task? Whether any external execution already started has stopped will be checked separately."
  },
  "progress.unknown": {
    "ko": "미확인",
    "en": "Unknown"
  }
} as const;
