export const reviewPanelsMessages = {
  "absence.title": { "ko": "없다는 주장에 대한 문자 검사", "en": "Literal checks of absence claims" },
  "absence.limitation": { "ko": "AI가 선언한 문자열을 기록된 추출 범위에서만 검사했습니다. 자료 전체에 없다는 뜻이나 의미·사실의 검증이 아닙니다.", "en": "Only strings declared by the model were checked within the recorded extracted scopes. This does not establish absence from the whole source or verify meaning or facts." },
  "absence.empty": { "ko": "검사할 부재 주장이 선언되지 않았습니다. 모든 문장을 검사했다는 뜻은 아닙니다.", "en": "No absence claims were declared for checking. This does not mean every sentence was checked." },
  "absence.inInput": { "ko": "AI가 받은 자료에 있는데 없다고 함", "en": "The model claimed absence of a string present in its input" },
  "absence.outsideInput": { "ko": "AI에게 주지 않은 자료에 있음", "en": "Found in allowed material outside the model input" },
  "absence.inputUnchecked": { "ko": "허용 자료에서 찾음 · AI 입력 범위는 미확인", "en": "Found in allowed material; the model input scope is unchecked" },
  "absence.notFound": { "ko": "검사한 범위에서 못 찾음", "en": "Not found within the checked scope" },
  "absence.found": { "ko": "찾음", "en": "Found" },
  "absence.unsupported": { "ko": "미검사 · 원문 문자열 선언 또는 식별자 확인 필요", "en": "Unsupported check; verify the source-literal declaration or identifier" },
  "absence.notChecked": { "ko": "미검사 · 확인 불가", "en": "Not checked; undetermined" },
  "absence.inputScope": { "ko": "AI 입력", "en": "Model input" },
  "absence.allowedScope": { "ko": "조사 시작 시 허용된 추출 자료", "en": "Allowed extracted material at investigation start" },
  "absence.notDelivered": { "ko": "발견한 추가 근거를 다음 AI 입력에 전달하지 못했습니다.", "en": "The additional witness was not delivered to the next model input." },
  "numeric.title": { "ko": "원문 숫자로 계산한 값", "en": "Values calculated from source numbers" },
  "numeric.limitation": { "ko": "인과·복잡도 판정 아님. 원문에 적힌 숫자의 산술이며 측정 타당성이나 공식 기준 충족을 뜻하지 않습니다. 표시값은 소수 여섯 자리로 반올림했습니다.", "en": "Not a causal or computational-complexity determination. Arithmetic on printed values does not establish measurement validity or compliance with official criteria. Displayed values are rounded to six decimal places." },
  "numeric.series": { "ko": "계열", "en": "Series" },
  "numeric.interval": { "ko": "크기 구간", "en": "Size interval" },
  "numeric.operation": { "ko": "계산", "en": "Operation" },
  "numeric.value": { "ko": "계산값", "en": "Calculated value" },
  "numeric.source": { "ko": "원문", "en": "Source" },
  "numeric.ratio": { "ko": "값 비율", "en": "Value ratio" },
  "numeric.difference": { "ko": "값 차이", "en": "Value difference" },
  "numeric.slope": { "ko": "구간 log-log 기울기", "en": "Interval log-log slope" },
  "numeric.notCalculated": { "ko": "계산하지 않음", "en": "Not calculated" },
  "numeric.unsupported": { "ko": "구획·수치·단위가 명확하지 않아 계산하지 않은 항목이 있습니다.", "en": "Some entries were not calculated because their grouping, numbers or units were unclear." },
  "numeric.notDelivered": { "ko": "일부 계산 기록은 모델 입력에 포함되지 않았습니다.", "en": "Some calculation records were not included in the model input." },
  "reviewDialog.required": {
    "ko": "이유를 하나 이상 고르세요.",
    "en": "Choose at least one reason."
  },
  "reviewDialog.title": {
    "ko": "판단 재검토 요청",
    "en": "Request assessment review"
  },
  "reviewDialog.notice": {
    "ko": "AI가 근거를 다시 살펴 유지·변경·보류를 판단합니다. 이 요청만으로 판단 값이 바뀌지는 않습니다. 조사가 진행 중이면 끝난 뒤에 반영합니다.",
    "en": "AI will review the evidence again and decide whether to retain, change, or hold the assessment. The request alone changes no assessment value. If research is running, it is applied after that run finishes."
  },
  "reviewDialog.failed": {
    "ko": "재검토 요청을 보내지 못했습니다.",
    "en": "The review request could not be sent."
  },
  "reviewDialog.send": {
    "ko": "재검토 요청 보내기",
    "en": "Send review request"
  },
  "hypothesisFields.unconfirmed": {
    "ko": "원인 미확인 — 지지된 가설이 없습니다(원인이 없다는 뜻이 아닙니다)",
    "en": "Cause unconfirmed — no hypothesis is supported; this does not mean no cause exists"
  },
  "hypothesisFields.gaps": {
    "ko": "남은 빈틈",
    "en": "Remaining gaps"
  },
  "hypothesisFields.inference": {
    "ko": "추론(가설)",
    "en": "Inference (hypothesis)"
  },
  "hypothesisFields.assumptions": {
    "ko": "가정",
    "en": "Assumptions"
  },
  "hypothesisFields.predictions": {
    "ko": "맞다면 보일 관찰",
    "en": "Observations expected if true"
  },
  "hypothesisFields.missing": {
    "ko": "모자란 근거",
    "en": "Missing evidence"
  },
  "reviewStatus.all": {
    "ko": "가설 전체",
    "en": "All hypotheses"
  },
  "reviewStatus.failed": {
    "ko": "재검토 요청을 보내지 못했습니다",
    "en": "The review request could not be sent"
  },
  "reviewStatus.resend": {
    "ko": "다시 보내기",
    "en": "Send again"
  },
  "reviewStatus.running": {
    "ko": "재검토 중",
    "en": "Review in progress"
  },
  "reviewStatus.afterCurrent": {
    "ko": "· 현재 조사 뒤에 반영",
    "en": "· Applied after the current investigation"
  },
  "reviewStatus.request": {
    "ko": "재검토 요청",
    "en": "Request review"
  },
  "facts.title": {
    "ko": "AI 검토 요약(인용 위치만 확인)",
    "en": "AI review summary (citation locations checked only)"
  },
  "facts.documentOnly": {
    "ko": "· AI 검토자가 정리한 문장입니다. 인용한 위치만 확인했고, 내용이 사실인지와 기준 충족은 확인하지 않았습니다",
    "en": "· Written by an AI reviewer; only the cited locations were checked, not whether the statements are true or a criterion is met"
  },
  "facts.legacy": {
    "ko": "이 결과에는 정리된 AI 검토 요약이 없습니다(이전 형식).",
    "en": "This older result format has no structured AI review summary."
  },
  "facts.empty": {
    "ko": "정리된 AI 검토 요약 없음",
    "en": "No structured AI review summary"
  },
  "facts.notFound": {
    "ko": "AI가 찾아봤지만 없다고 함",
    "en": "AI searched and reports not found"
  },
  "sourceTime.correctReason": {
    "ko": "날짜 근거 정정",
    "en": "Correct date evidence"
  },
  "sourceTime.advanced": {
    "ko": "고급 · 자료의 기준시점",
    "en": "Advanced · source cutoff"
  },
  "sourceTime.state": {
    "ko": "기준시점 상태",
    "en": "Cutoff status"
  },
  "sourceTime.notice": {
    "ko": "상태 드롭다운만으로 이후 자료를 합격 처리할 수 없습니다. 사용자가 확인한 경우에만 바꿉니다.",
    "en": "A status dropdown alone cannot make later material eligible. Change it only after user verification."
  },
  "sourceTime.eligible": {
    "ko": "기준시점까지 존재로 정정",
    "en": "Mark as existing by the cutoff"
  },
  "sourceTime.after": {
    "ko": "기준시점보다 후로 정정",
    "en": "Mark as after the cutoff"
  },
  "sourceTime.unknown": {
    "ko": "미확인으로 되돌림",
    "en": "Return to unverified"
  },
  "sameHypothesis.otherResearch": {
    "ko": "같은 가설로 표시된 다른 조사",
    "en": "Other investigations marked as the same hypothesis"
  },
  "sameHypothesis.disconnect": {
    "ko": "연결 끊기",
    "en": "Disconnect"
  },
  "sameHypothesis.indirect": {
    "ko": "다른 가설을 거쳐 이어져 있습니다.",
    "en": "Connected through another hypothesis."
  },
  "sameHypothesis.choose": {
    "ko": "이 가설과 같은 가설을 고르세요(같은 추적표 줄이 위에 있습니다)",
    "en": "Choose the same hypothesis (those from the same trace row appear first)"
  },
  "sameHypothesis.apply": {
    "ko": "표시하기",
    "en": "Apply mark"
  },
  "testOrder.title": {
    "ko": "시험 순서",
    "en": "Test order"
  },
  "testOrder.nextTests": {
    "ko": "다음에 해 볼 시험의 순서",
    "en": "Order of tests to try next"
  },
  "testOrder.noProcedure": {
    "ko": "시험 내용이 기록되지 않았습니다",
    "en": "No test procedure is recorded"
  },
  "testOrder.costUnknown": {
    "ko": "비용 미상",
    "en": "Cost unknown"
  },
  "testOrder.cost": {
    "ko": "비용 추정",
    "en": "Estimated cost"
  },
  "testOrder.tieNotice": {
    "ko": "순서를 가를 차이가 없어 만든 순서로 놓았습니다.",
    "en": "No ordering difference was found, so creation order is used."
  },
  "testOrder.tie": {
    "ko": "동점 · 만든 순서",
    "en": "Tied · creation order"
  },
  "testOrder.singleNotice": {
    "ko": "결과 한 건으로 확정하지 않습니다. 후보에는 그대로 셉니다.",
    "en": "One result does not settle the hypothesis. It remains in the candidate count."
  },
  "liveProgress.noCandidates": {
    "ko": "현재 연결 범위에서 선택된 근거 후보가 없습니다.",
    "en": "No evidence candidates were selected from the current connected scope."
  },
  "liveProgress.title": {
    "ko": "연구 진행 상황",
    "en": "Research progress"
  },
  "liveProgress.now": {
    "ko": "지금",
    "en": "Now"
  },
  "liveProgress.why": {
    "ko": "왜",
    "en": "Why"
  },
  "liveProgress.checked": {
    "ko": "확인한 과정",
    "en": "Checks performed"
  },
  "liveProgress.scope": {
    "ko": "근거 범위",
    "en": "Evidence scope"
  },
  "liveProgress.details": {
    "ko": "작업 상세",
    "en": "Work details"
  },
  "revision.selected": {
    "ko": "선택한 기록",
    "en": "Selected record"
  },
  "revision.current": {
    "ko": "현재",
    "en": "Current"
  },
  "revision.difference": {
    "ko": "선택한 기록과 현재의 차이",
    "en": "Difference between the selected record and current version"
  },
  "revision.compare": {
    "ko": "변경 비교",
    "en": "Compare changes"
  },
  "revision.notice": {
    "ko": "저장된 두 버전의 내용입니다. 변경 이유가 기록되지 않은 경우 추정하지 않습니다.",
    "en": "These are the two saved versions. Unrecorded reasons for changes are not inferred."
  },
  "revision.partial": {
    "ko": "일부 변경의 의미를 분류하지 못했습니다. 확인 가능한 차이만 표시합니다.",
    "en": "Some changes could not be classified. Only verifiable differences are shown."
  },
  "revision.same": {
    "ko": "확인한 내용은 같습니다.",
    "en": "The verified content is unchanged."
  },
  "revision.content": {
    "ko": "내용 변경",
    "en": "Content changes"
  },
  "revision.location": {
    "ko": "변경 위치",
    "en": "Change location"
  },
  "revision.diagnostics": {
    "ko": "비교 진단",
    "en": "Comparison diagnostics"
  },
  "citation.ready": {
    "ko": "인용 준비됨",
    "en": "Ready for citation"
  },
  "citation.afterCutoff": {
    "ko": "기준시점보다 이후라 인용에 넣지 않습니다.",
    "en": "The source is after the cutoff and will not be included as a citation."
  },
  "citation.prohibited": {
    "ko": "사용 금지 자료입니다. 시점 확인으로 해제되지 않습니다.",
    "en": "Use of this source is prohibited. Verifying its date does not remove that restriction."
  },
  "citation.confirmed": {
    "ko": "시점을 확인했습니다. 인용 준비됨",
    "en": "Timing verified. Ready for citation."
  },
  "actionDraft.fromTest": {
    "ko": "이 시험으로 행동 요청 준비",
    "en": "Prepare an action request from this test"
  },
  "actionDraft.noModel": {
    "ko": "요청을 준비할 뿐 승인이 아닙니다. 모델을 부르지 않습니다.",
    "en": "Prepares a request without granting approval or calling a model."
  }
} as const;
