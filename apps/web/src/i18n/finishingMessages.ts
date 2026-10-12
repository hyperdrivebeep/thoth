export const finishingMessages = {
  "progress.tableOnly": {
    "ko": "표 숫자만 인용",
    "en": "Cite table numbers only"
  },
  "progress.noGuess": {
    "ko": "추측 금지",
    "en": "No guessing"
  },
  "progress.withLocator": {
    "ko": "원문 위치와 함께 답",
    "en": "Answer with original source locations"
  },
  "progress.explainMissing": {
    "ko": "확인 안 되면 부족 이유",
    "en": "Explain what is missing when unverified"
  },
  "progress.elapsedUnknown": {
    "ko": "경과 미확인",
    "en": "Elapsed time unknown"
  },
  "progress.noFirstByte": {
    "ko": "첫 글자 아직 없음",
    "en": "No first byte yet"
  },
  "progress.pausing": {
    "ko": "현재 단계를 마치고 멈추는 중입니다.",
    "en": "Finishing the current stage before pausing."
  },
  "progress.paused": {
    "ko": "작업이 일시정지되어 있습니다.",
    "en": "The task is paused."
  },
  "progress.externalUnknown": {
    "ko": "외부 실행의 종료 여부를 아직 확인하지 못했습니다.",
    "en": "External execution termination has not yet been verified."
  },
  "usage.none": {
    "ko": "이 결과의 사용량 기록 없음",
    "en": "No usage recorded for this result"
  },
  "usage.lessMinute": {
    "ko": "1분 미만",
    "en": "Less than one minute"
  },
  "usage.timeUnknown": {
    "ko": "시간 미확인",
    "en": "Duration unknown"
  },
  "trace.noRule": {
    "ko": "판정 규칙 없음",
    "en": "No assessment rule"
  },
  "trace.noValue": {
    "ko": "값 없음",
    "en": "No value"
  },
  "trace.failedCheck": {
    "ko": "미달인 기준 줄에서 시험이 유효했는지, 다른 원인 후보가 있는지 확인합니다.",
    "en": "On each unmet criterion row, check whether the test was valid and whether alternative causes exist."
  },
  "trace.excelFix": {
    "ko": "원래 ID로 되돌리고, ID 칸을 '텍스트' 서식으로 바꾼 뒤 다시 저장하세요.",
    "en": "Restore the original ID, format the ID field as text, then save again."
  },
  "trace.invalidGraph": {
    "ko": "반영하면 연결이 맞지 않는 곳이 생깁니다.",
    "en": "Applying this would create inconsistent links."
  },
  "trace.checkLinks": {
    "ko": "연결 줄의 양쪽 ID와 종류가 추적표에 있는지 확인하세요.",
    "en": "Check that both IDs and types in each link exist in the trace table."
  },
  "labels.targetQuoted": {
    "ko": "대상:",
    "en": "Target:"
  },
  "labels.evidenceSuffix": {
    "ko": " · 근거 ",
    "en": " · Evidence "
  },
  "labels.aiReview": {
    "ko": "(AI 검토 기준)",
    "en": "(based on AI review)"
  },
  "labels.command": {
    "ko": "서버 터미널에서 다음 명령을 실행하세요: ",
    "en": "Run this command in the server terminal:"
  }
} as const;
