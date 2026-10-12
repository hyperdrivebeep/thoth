export const apiNoticesMessages = {
  "api.jsonObject": {
    "ko": "입력은 JSON 객체여야 합니다.",
    "en": "Input must be a JSON object."
  },
  "api.projectMismatch": {
    "ko": "선택한 프로젝트와 project_id가 일치해야 합니다.",
    "en": "project_id must match the selected project."
  },
  "api.noOperation": {
    "ko": "비동기 operation 식별자가 없습니다.",
    "en": "The asynchronous operation identifier is missing."
  },
  "api.analysisFailed": {
    "ko": "분석 operation이 실패했습니다.",
    "en": "The analysis operation failed."
  },
  "api.analysisCancelled": {
    "ko": "사용자가 분석을 취소했습니다.",
    "en": "The user cancelled the analysis."
  },
  "api.restoreTarget": {
    "ko": "복원 대상의 연결을 확인하지 못했습니다.",
    "en": "The restoration target link could not be verified."
  },
  "api.restorePreviewIncomplete": {
    "ko": "복원 미리보기가 완료되지 않았습니다.",
    "en": "The restoration preview did not complete."
  },
  "api.restoreReviewItems": {
    "ko": "다시 검토할 항목",
    "en": "Items to review again"
  },
  "api.restoreUnavailable": {
    "ko": "현재 복원을 적용할 수 없습니다. 새 미리보기를 확인하세요.",
    "en": "Restoration cannot be applied now. Review a new preview."
  },
  "api.restoreRecordUnknown": {
    "ko": "복원 결과의 새 버전과 저장 기록을 확인하지 못했습니다.",
    "en": "The new version and saved restoration record could not be verified."
  },
  "api.restoreUnconfirmed": {
    "ko": "적용 완료를 확인하지 못했습니다. 원 요청의 결과를 확인하세요.",
    "en": "Completion could not be confirmed. Check the original request's result."
  },
  "api.restoreBinding": {
    "ko": "원 복원 요청의 연결을 확인하지 못했습니다.",
    "en": "The original restoration request link could not be verified."
  },
  "api.restoreNotApplied": {
    "ko": "복원 요청이 적용되지 않았습니다.",
    "en": "The restoration request was not applied."
  },
  "api.restoreStateUnknown": {
    "ko": "복원 요청의 상태를 확인하지 못했습니다.",
    "en": "The restoration request state could not be verified."
  },
  "api.historyIncomplete": {
    "ko": "이력 조회가 완료되지 않았습니다.",
    "en": "The history query did not complete."
  },
  "api.historyProject": {
    "ko": "기록의 프로젝트 연결을 확인하지 못했습니다.",
    "en": "The record's project link could not be verified."
  },
  "api.historyResult": {
    "ko": "과거 답변의 요청·결과 연결을 확인하지 못했습니다.",
    "en": "The past answer's request-to-result link could not be verified."
  },
  "api.historyPair": {
    "ko": "당시 질문과 답변",
    "en": "Question and answer at that time"
  },
  "api.historySelection": {
    "ko": "선택한 기록의 연결을 확인하지 못했습니다.",
    "en": "The selected record's link could not be verified."
  },
  "sourceTime.title": {
    "ko": "자료 시점 확인",
    "en": "Verify source timing"
  },
  "sourceTime.automaticFailed": {
    "ko": "이 자료의 시점을 자동 확인하지 못했습니다.",
    "en": "The source timing could not be verified automatically."
  },
  "sourceTime.question": {
    "ko": "이 판은, 표시된 판단 기준시점까지 존재하던 자료입니까?",
    "en": "Did this version exist by the displayed assessment cutoff?"
  },
  "sourceTime.existed": {
    "ko": "기준시점까지 존재",
    "en": "Existed by the cutoff"
  },
  "sourceTime.later": {
    "ko": "기준시점보다 후",
    "en": "After the cutoff"
  },
  "sourceTime.skip": {
    "ko": "모르겠음 / 이번에는 쓰지 않음",
    "en": "Unknown / do not use this time"
  },
  "memorySwitch.help": {
    "ko": "끄면 저장된 기억과 정정은 그대로 두고, 앞으로의 조사에는 기억을 넣지 않습니다.",
    "en": "Turning this off preserves stored memories and corrections but excludes them from future investigations."
  },
  "memorySwitch.expansion": {
    "ko": "검색어 넓히기(조사마다 모델을 한 번 더 부릅니다)",
    "en": "Expand search terms (one additional model call per investigation)"
  },
  "memorySwitch.expansionHelp": {
    "ko": "질문과 다른 말로 저장된 기억도 찾도록 모델이 검색어를 덧붙입니다. 끄면 질문에 쓴 말로만 찾고, 저장된 기억은 그대로입니다.",
    "en": "A model adds search terms to find memories written in different words. When disabled, search uses only the question's words; stored memories are unchanged."
  },
  "memorySwitch.failed": {
    "ko": "기억 사용 설정을 읽지 못했습니다.",
    "en": "The memory-use setting could not be read."
  },
  "memorySwitch.label": {
    "ko": "조사에 기억 사용",
    "en": "Use memory in research"
  },
  "answerHold.title": {
    "ko": "결론 상태",
    "en": "Conclusion state"
  },
  "answerHold.coverageMissing": {
    "ko": "답변 점검 항목 정보를 아직 읽지 못했습니다",
    "en": "Answer-check information is not available yet"
  },
  "approvalView.approved": {
    "ko": "승인됨 · 이 내용으로 유효",
    "en": "Approved · valid for this content"
  },
  "approvalView.pending": {
    "ko": "승인 대기",
    "en": "Awaiting approval"
  },
  "approvalView.rejected": {
    "ko": "거절됨",
    "en": "Rejected"
  },
  "approvalView.expired": {
    "ko": "만료됨",
    "en": "Expired"
  },
  "approvalView.used": {
    "ko": "사용됨",
    "en": "Used"
  },
  "approvalView.invalid": {
    "ko": "승인 무효 · 내용이 바뀜",
    "en": "Approval invalid · content changed"
  }
} as const;
