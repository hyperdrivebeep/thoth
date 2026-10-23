export const modelHelpMessages = {
  "modelHelp.retry": {
    "ko": "모델 호출이 중간에 끊기면 그 호출만 최대 2번 자동으로 다시 시도합니다. 토큰이 더 쓰입니다. 끄면 끊긴 결과 카드의 \"이어서 조사\"로 다시 할 수 있습니다.",
    "en": "If a model call is interrupted, only that call is retried automatically up to twice, using additional tokens. Turn this off to resume manually from the interrupted result card."
  },
  "modelHelp.retryReadFailed": {
    "ko": "자동 재시도 설정을 읽지 못했습니다.",
    "en": "The automatic retry setting could not be read."
  },
  "modelHelp.retryLabel": {
    "ko": "끊긴 모델 호출 자동 재시도",
    "en": "Automatically retry interrupted model calls"
  },
  "modelHelp.changeFailed": {
    "ko": "설정을 바꾸지 못했습니다. 화면을 다시 읽었으니 다시 시도해 주세요.",
    "en": "The setting could not be changed. The view was refreshed; try again."
  },
  "modelHelp.hypothesis": {
    "ko": "켜면 가설을 만드는 호출에서 각 시험의 가설별 예상 결과와 기각 조건을 함께 받아 시험 순서를 정합니다. 비어 있는 예상 결과를 채우기 위해 추가 모델 호출이 생길 수 있습니다. 답이 길어져 토큰이 더 쓰일 수 있으며, 모델이 낸 내용은 미확정 제안입니다. 저장된 선택이 없으면 기본으로 켜집니다. 이전에 끈 설정은 그대로 유지됩니다.",
    "en": "Collects each test's expected results for each hypothesis and refutation conditions during hypothesis generation. Filling missing expected results may require an additional model call. Longer answers can use more tokens. Model output remains an unconfirmed proposal. On by default when no choice has been saved. Saved off choices stay off."
  },
  "modelHelp.hypothesisReadFailed": {
    "ko": "가설 생성 설정을 읽지 못했습니다.",
    "en": "The hypothesis generation setting could not be read."
  },
  "modelHelp.hypothesisLabel": {
    "ko": "가설 만들 때 예상 결과와 기각 조건도 함께 받기(고급)",
    "en": "Include expected results and refutation conditions (advanced)"
  },
  "modelHelp.staleCatalog": {
    "ko": "모델 목록을 새로 받지 못해 마지막으로 확인한 목록을 보여 줍니다. 저장된 선택은 그대로입니다.",
    "en": "The model list could not be refreshed. The last verified list is shown; your saved selection is unchanged."
  }
} as const;
