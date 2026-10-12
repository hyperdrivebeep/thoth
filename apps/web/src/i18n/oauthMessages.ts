export const oauthMessages = {
  "oauth.idle": {
    "ko": "이 로그인 시도를 더는 확인할 수 없습니다. 계정 상태를 다시 확인하세요.",
    "en": "This login attempt is no longer available. Refresh the account status."
  },
  "oauth.catalogLoading": {
    "ko": "로그인이 확인됐습니다. 모델 목록을 받는 중입니다.",
    "en": "Login is confirmed. Loading the model list."
  },
  "oauth.browser": {
    "ko": "브라우저에서 로그인을 마쳐 주세요. 끝나면 자동으로 확인합니다.",
    "en": "Finish login in your browser. Completion is checked automatically."
  },
  "oauth.connected": {
    "ko": "로그인됐습니다. 실제 모델 응답은 첫 연구에서 확인합니다.",
    "en": "Logged in. Actual model responses will be verified on the first research run."
  },
  "oauth.denied": {
    "ko": "로그인이 거부됐습니다. 새 로그인은 직접 선택할 때만 시작됩니다.",
    "en": "Login was denied. A new login starts only when you select it."
  },
  "oauth.expired": {
    "ko": "로그인 시도의 유효 시간이 끝났습니다.",
    "en": "This login attempt expired."
  },
  "oauth.cancelled": {
    "ko": "이 로그인 시도를 취소했습니다. 기존 인증과 API 키는 유지됩니다.",
    "en": "This login attempt was cancelled. Existing credentials and API keys are preserved."
  },
  "oauth.failed": {
    "ko": "로그인 시도에 실패했습니다. 계정 상태를 확인하세요.",
    "en": "Login failed. Check the account status."
  },
  "oauth.installed": {
    "ko": "도구를 설치했습니다. 이제 로그인을 시작할 수 있습니다.",
    "en": "The tool is installed. You can now start login."
  },
  "oauth.invalidStart": {
    "ko": "로그인 시작 응답의 시도 ID와 상태를 확인하지 못했습니다. 자동으로 다시 시작하지 않습니다.",
    "en": "The login attempt ID and state could not be verified. Login will not restart automatically."
  },
  "oauth.startUnknown": {
    "ko": "로그인 시작 결과를 확인하지 못했습니다. 새 시도는 직접 선택할 때만 시작됩니다.",
    "en": "The result of starting login is unknown. A new attempt starts only when you select it."
  },
  "oauth.statusUnknown": {
    "ko": "이 로그인 시도의 상태를 확인하지 못했습니다. 자동 조회를 멈췄습니다.",
    "en": "This login attempt's status could not be verified. Automatic polling has stopped."
  },
  "oauth.noCatalog": {
    "ko": "로그인됐지만 모델 목록을 불러오지 못했습니다. 연결 상태를 확인한 뒤 다시 시도하세요.",
    "en": "Login succeeded, but the model list could not be loaded. Check the connection and try again."
  },
  "oauth.refreshUnknown": {
    "ko": "로그인 시도는 완료됐지만 계정·모델 목록을 다시 읽지 못했습니다. 연결 상태를 직접 확인하세요.",
    "en": "The login attempt finished, but account and model information could not be refreshed. Check the connection manually."
  },
  "oauth.pollFailed": {
    "ko": "상태 조회에 실패했습니다. 자동 조회를 멈췄으며 새 로그인은 시작하지 않았습니다.",
    "en": "Status polling failed and has stopped. No new login was started."
  },
  "oauth.cancelUnknown": {
    "ko": "취소 결과를 확인하지 못했습니다. 같은 시도의 상태를 직접 확인하세요.",
    "en": "Cancellation could not be confirmed. Check the same attempt's status manually."
  },
  "oauth.cancelNotRetried": {
    "ko": "취소 결과를 확인하지 못했습니다. 자동으로 다시 취소하지 않습니다.",
    "en": "Cancellation could not be confirmed. It will not be retried automatically."
  },
  "oauth.manualUnknown": {
    "ko": "수동 응답의 접수 상태를 확인하지 못했습니다. 입력값은 화면에서 지웠습니다.",
    "en": "Acceptance of the manual response could not be confirmed. Its input was cleared from the screen."
  },
  "oauth.manualFailed": {
    "ko": "수동 응답 결과를 확인하지 못했습니다. 입력값은 화면에서 지웠습니다.",
    "en": "The manual response result could not be verified. Its input was cleared from the screen."
  },
  "oauth.unsupported": {
    "ko": "이 PC에서는 계정 로그인을 시작할 수 없습니다. API 키로 연결하세요.",
    "en": "Account login cannot start on this computer. Connect with an API key."
  },
  "oauth.workspaceNeeded": {
    "ko": "작업 공간을 확인한 뒤 로그인을 시작할 수 있습니다.",
    "en": "Login can start after the workspace is verified."
  },
  "oauth.openPage": {
    "ko": "로그인 페이지 열기",
    "en": "Open login page"
  },
  "oauth.checkTime": {
    "ko": "남은 상태 확인 시간",
    "en": "Time remaining for status checks"
  },
  "oauth.validTime": {
    "ko": "남은 유효 시간",
    "en": "Time until expiry"
  },
  "oauth.checkManually": {
    "ko": "상태 직접 확인",
    "en": "Check status manually"
  },
  "oauth.cancel": {
    "ko": "이 로그인 취소",
    "en": "Cancel this login"
  },
  "oauth.manualInput": {
    "ko": "코드·리디렉션 URL 직접 입력",
    "en": "Enter a code or redirect URL manually"
  },
  "oauth.claudeResponse": {
    "ko": "Claude 인증 응답",
    "en": "Claude authentication response"
  },
  "oauth.submit": {
    "ko": "이 시도에 제출",
    "en": "Submit to this attempt"
  },
  "oauth.technical": {
    "ko": "기술 정보",
    "en": "Technical details"
  },
  "oauth.canAttempt": {
    "ko": "시도 가능",
    "en": "Eligible to attempt"
  },
  "oauth.notReady": {
    "ko": "준비 전",
    "en": "Not ready"
  },
  "oauth.verified": {
    "ko": "확인됨",
    "en": "Verified"
  },
  "oauth.unverified": {
    "ko": "미검증",
    "en": "Unverified"
  },
  "xai.pending": {
    "ko": "xAI 승인을 기다리고 있습니다. 로그인 시작만으로 연결된 것은 아닙니다.",
    "en": "Waiting for xAI approval. Starting login does not establish a connection."
  },
  "xai.slowDown": {
    "ko": "xAI 승인을 기다리고 있습니다. 상태 확인 간격을 늘렸습니다.",
    "en": "Waiting for xAI approval. Status checks are now less frequent."
  },
  "xai.connected": {
    "ko": "xAI 로그인이 확인됐습니다. 사용할 모델을 직접 선택하세요.",
    "en": "xAI login is confirmed. Select the model you want to use."
  },
  "xai.denied": {
    "ko": "xAI 로그인이 거부됐습니다. 다시 시작하려면 새 코드를 요청하세요.",
    "en": "xAI login was denied. Request a new code to try again."
  },
  "xai.expired": {
    "ko": "기기 코드의 유효 시간이 끝났습니다. 다시 시작하려면 새 코드를 요청하세요.",
    "en": "The device code expired. Request a new code to try again."
  },
  "xai.cancelled": {
    "ko": "이 로그인 요청을 취소했습니다. 기존 API 키 연결은 유지됩니다.",
    "en": "This login request was cancelled. Existing API key connections are preserved."
  },
  "xai.failed": {
    "ko": "xAI 로그인에 실패했습니다. 연결 상태를 확인한 뒤 다시 시작하세요.",
    "en": "xAI login failed. Check the connection before trying again."
  },
  "xai.requestUnknown": {
    "ko": "현재 로그인 요청을 확인할 수 없습니다. 계정 상태를 다시 확인하세요.",
    "en": "The current login request could not be verified. Refresh the account status."
  },
  "xai.invalidStart": {
    "ko": "xAI 로그인 시작 응답을 확인하지 못했습니다. 새 로그인은 자동으로 시작하지 않습니다.",
    "en": "The xAI login response could not be verified. A new login will not start automatically."
  },
  "xai.startUnknown": {
    "ko": "xAI 로그인 시작 결과를 확인하지 못했습니다. 계정 상태를 확인한 뒤 다시 시도하세요.",
    "en": "The result of starting xAI login is unknown. Check the account before trying again."
  },
  "xai.statusUnknown": {
    "ko": "현재 로그인 요청의 상태를 확인하지 못했습니다. 새 로그인은 자동으로 시작하지 않습니다.",
    "en": "The current login request's status is unknown. A new login will not start automatically."
  },
  "xai.refreshFailed": {
    "ko": "xAI 로그인은 확인됐지만 계정·모델 목록을 다시 읽지 못했습니다. 연결 상태를 직접 확인하세요.",
    "en": "xAI login is confirmed, but account and model information could not be refreshed. Check the connection manually."
  },
  "xai.pollFailed": {
    "ko": "로그인 상태 조회에 실패했습니다. 자동 확인을 멈췄습니다. 새 로그인은 시작하지 않았습니다.",
    "en": "Login status checks failed and have stopped. No new login was started."
  },
  "xai.cancelUnknown": {
    "ko": "취소 결과를 확인하지 못했습니다. 같은 로그인 ID의 상태를 직접 확인하세요.",
    "en": "Cancellation could not be confirmed. Check the status of the same login ID manually."
  },
  "xai.unsupported": {
    "ko": "이 PC에서는 xAI 로그인을 시작할 수 없습니다. API 키로 연결하세요.",
    "en": "xAI login cannot start on this computer. Connect with an API key."
  },
  "xai.loggedIn": {
    "ko": "xAI 로그인됨",
    "en": "Logged in to xAI"
  },
  "xai.restart": {
    "ko": "xAI 로그인 다시 시작",
    "en": "Restart xAI login"
  },
  "xai.login": {
    "ko": "xAI로 로그인",
    "en": "Log in to xAI"
  },
  "xai.workspaceNeeded": {
    "ko": "작업 공간을 확인한 뒤 xAI 로그인을 시작할 수 있습니다.",
    "en": "xAI login can start after the workspace is verified."
  },
  "xai.openApproval": {
    "ko": "xAI 승인 페이지 열기",
    "en": "Open xAI approval page"
  },
  "web.execution.ready": {
    "ko": "실행: 준비됨",
    "en": "Execution: ready"
  },
  "web.execution.off": {
    "ko": "실행: 꺼짐",
    "en": "Execution: off"
  },
  "web.execution.policy": {
    "ko": "실행: 권한 불일치",
    "en": "Execution: permission mismatch"
  },
  "web.execution.grant": {
    "ko": "실행: grant 불일치",
    "en": "Execution: grant mismatch"
  },
  "web.execution.unavailable": {
    "ko": "실행: 구현 없음",
    "en": "Execution: unavailable"
  },
  "web.execution.consent": {
    "ko": "실행: 워크스페이스 미허용",
    "en": "Execution: workspace consent missing"
  },
  "web.execution.blocked": {
    "ko": "실행: 제한됨",
    "en": "Execution: restricted"
  }
} as const;
