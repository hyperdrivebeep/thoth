export const workspaceDetailMessages = {
  "workspace.questionEvidence": {
    "ko": "질문에서 근거로, 근거에서 다음 행동으로.",
    "en": "From questions to evidence, and from evidence to the next action."
  },
  "workspace.continueHeading": {
    "ko": "연구를 이어가거나 새 문제를 시작하세요.",
    "en": "Continue your research or start a new problem."
  },
  "workspace.authorizedRead": {
    "ko": "권한 있는 저장 연구는 계속 읽을 수 있습니다.",
    "en": "You can still read saved research within your access permissions."
  },
  "workspace.readOnlyHold": {
    "ko": "권한 있는 저장 연구는 계속 읽을 수 있지만 새 모델 실행과 동의 변경은 보류합니다.",
    "en": "Saved research remains available within your permissions. New model runs and consent changes are on hold."
  },
  "workspace.resumeFailed": {
    "ko": "이어서 조사를 시작하지 못했습니다.",
    "en": "Research could not be resumed."
  },
  "workspace.restoreScope": {
    "ko": "다른 주소·포트·브라우저 프로필 또는 작업 공간의 선택과 초안은 자동 병합되지 않습니다. 프로젝트를 직접 선택하면 현재 위치를 새로 저장합니다.",
    "en": "Selections and drafts from other addresses, ports, browser profiles, or workspaces are not merged automatically. Selecting a project saves the new location."
  },
  "workspace.existingRead": {
    "ko": "기존 프로젝트가 있으면 권한 있는 저장 연구를 열 수 있습니다.",
    "en": "If a project already exists, you can open its saved research within your access permissions."
  },
  "workspace.preparing": {
    "ko": "준비 확인 중…",
    "en": "Checking readiness…"
  },
  "workspace.readinessFailed": {
    "ko": "서버 준비 확인 실패",
    "en": "Server readiness check failed"
  },
  "workspace.readinessAdvice": {
    "ko": "서버 준비 상태를 확인하지 못했습니다. 연결 상태를 확인하고 다시 시도하세요.",
    "en": "Server readiness could not be checked. Check the connection and try again."
  },
  "workspace.loadingSaved": {
    "ko": "이 워크스페이스의 저장된 작업을 확인하는 중…",
    "en": "Checking saved tasks in this workspace…"
  },
  "workspace.checkingProjects": {
    "ko": "읽을 수 있는 프로젝트를 확인하는 중…",
    "en": "Checking accessible projects…"
  },
  "workspace.accessFailed": {
    "ko": "프로젝트 접근 범위를 확인하지 못했습니다. 저장된 작업을 새 프로젝트로 대체하지 않습니다.",
    "en": "Project access could not be verified. Saved work will not be replaced with a new project."
  },
  "workspace.savedUnavailable": {
    "ko": "이 워크스페이스의 저장된 연구를 지금 읽을 수 없습니다. 접근 상태를 확인하세요.",
    "en": "Saved research in this workspace is currently unavailable. Check your access."
  },
  "workspace.taskUnavailable": {
    "ko": "선택한 작업을 확인하지 못했습니다. 초안은 유지합니다.",
    "en": "The selected task could not be verified. Your draft is preserved."
  },
  "workspace.draftNotSent": {
    "ko": "저장된 초안은 자동 제출하지 않습니다.",
    "en": "Saved drafts are not submitted automatically."
  },
  "setup.researchIndependent": {
    "ko": "저장된 연구 기록은 이 설정 파일과 별개이며 권한이 있으면 계속 읽을 수 있습니다.",
    "en": "Saved research is separate from this settings file and remains accessible with the required permissions."
  },
  "model.unavailable": {
    "ko": "저장된 모델 선택을 현재 사용할 수 없습니다.",
    "en": "The saved model selection is currently unavailable."
  },
  "model.inheritedUnchanged": {
    "ko": " 이 작업은 프로젝트 모델 설정을 상속하며 별도 선택으로 대체하지 않았습니다.",
    "en": " This task inherits the project model settings; no separate selection has replaced them."
  },
  "model.keepSelection": {
    "ko": "기존 선택은 유지됩니다. 변경하려면 아래에서 모델을 직접 고르세요.",
    "en": "Your existing selection is preserved. To change it, choose a model below."
  },
  "model.modelLabel": {
    "ko": "모델",
    "en": "Model"
  },
  "model.effortLabel": {
    "ko": "추론강도",
    "en": "Reasoning effort"
  }
} as const;
