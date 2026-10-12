export const credentialMessages = {
  "connection.timeout": {
    "ko": "THOTH 서버가 제때 응답하지 않았습니다. 잠시 뒤 다시 시도하세요.",
    "en": "The THOTH server did not respond in time. Try again shortly."
  },
  "connection.offline": {
    "ko": "THOTH 서버에 연결하지 못했습니다. 서버가 실행 중인지 확인하세요.",
    "en": "Could not connect to the THOTH server. Check that it is running."
  },
  "connection.intro.openai": {
    "ko": "ChatGPT 계정으로 로그인하거나 OpenAI API 키로 연결합니다.",
    "en": "Sign in with a ChatGPT account or connect with an OpenAI API key."
  },
  "connection.intro.anthropic": {
    "ko": "Claude 계정으로 로그인하거나 Anthropic API 키로 연결합니다.",
    "en": "Sign in with a Claude account or connect with an Anthropic API key."
  },
  "connection.intro.xai": {
    "ko": "xAI 계정으로 로그인하거나 xAI API 키로 연결합니다.",
    "en": "Sign in with an xAI account or connect with an xAI API key."
  },
  "connection.intro.key": {
    "ko": "API 키로 연결합니다.",
    "en": "Connect with an API key."
  },
  "connection.checking": {
    "ko": "확인 중",
    "en": "Checking"
  },
  "connection.disconnected": {
    "ko": "연결 안 됨",
    "en": "Not connected"
  },
  "connection.connected": {
    "ko": "연결됨",
    "en": "Connected"
  },
  "connection.toolNeeded": {
    "ko": "도구 설치 필요",
    "en": "Tool installation needed"
  },
  "connection.loggingIn": {
    "ko": "로그인 중",
    "en": "Logging in"
  },
  "connection.browserLogin": {
    "ko": "브라우저에서 로그인을 마치면 자동으로 확인합니다.",
    "en": "Login will be checked automatically when you finish in the browser."
  },
  "connection.modelsNeeded": {
    "ko": "모델 확인 필요",
    "en": "Model list needed"
  },
  "connection.loadAfterLogin": {
    "ko": "로그인은 완료됐습니다. 모델 목록을 불러오면 바로 쓸 수 있습니다.",
    "en": "Login is complete. Load the model list to continue."
  },
  "connection.checkFailed": {
    "ko": "확인 실패",
    "en": "Check failed"
  },
  "connection.refreshAdvice": {
    "ko": "연결 상태를 확인하지 못했습니다. 연결 상태를 다시 확인하세요.",
    "en": "The connection status could not be verified. Refresh it and try again."
  },
  "credential.noRoute": {
    "ko": "연결 경로 없음",
    "en": "No connection route"
  },
  "credential.noCodex": {
    "ko": "Codex 실행 파일 없음",
    "en": "Codex executable missing"
  },
  "credential.noVersion": {
    "ko": "지원 버전 없음",
    "en": "Supported version missing"
  },
  "credential.reviewUpdate": {
    "ko": "업데이트 검토 필요",
    "en": "Update review required"
  },
  "credential.loginRequired": {
    "ko": "로그인 필요",
    "en": "Login required"
  },
  "credential.loginPending": {
    "ko": "로그인 진행 중",
    "en": "Login in progress"
  },
  "credential.noCatalog": {
    "ko": "모델 목록 없음",
    "en": "Model list unavailable"
  },
  "credential.canTry": {
    "ko": "실행 시도 가능 · 성공 미검증",
    "en": "Execution eligible · success unverified"
  },
  "credential.unverified": {
    "ko": "실행 미검증",
    "en": "Execution unverified"
  },
  "credential.checkFailed": {
    "ko": "연결 확인 실패",
    "en": "Connection check failed"
  },
  "credential.keyReady": {
    "ko": "키 등록됨 · 연결 준비",
    "en": "Key registered · ready to connect"
  },
  "credential.loginReady": {
    "ko": "로그인 상태 확인됨 · 연결 준비",
    "en": "Login confirmed · ready to connect"
  },
  "credential.none": {
    "ko": "아직 없음",
    "en": "None yet"
  },
  "credential.routeNeedsCheck": {
    "ko": "연결 경로 확인 필요",
    "en": "Connection route needs checking"
  },
  "credential.loginAndKey": {
    "ko": "로그인 상태 확인 · 키 등록됨",
    "en": "Login confirmed · key registered"
  },
  "credential.loginConfirmed": {
    "ko": "로그인 상태 확인됨",
    "en": "Login confirmed"
  },
  "credential.keyRegistered": {
    "ko": "키 등록됨",
    "en": "Key registered"
  },
  "credential.statusNeeded": {
    "ko": "연결 상태 확인 필요",
    "en": "Connection status needs checking"
  },
  "credential.thirdPartyUnverified": {
    "ko": "실제 제3자 사용 허용과 모델 실행 성공은 아직 확인되지 않았습니다.",
    "en": "Third-party usage permission and successful model execution have not yet been verified."
  },
  "credential.firstRunVerify": {
    "ko": "실제 모델 실행 성공은 첫 연구에서 확인합니다.",
    "en": "Actual model execution will be checked on the first research run."
  },
  "credential.localEligible": {
    "ko": "로컬 실행 시도 조건은 확인됐지만 실제 공급자 성공은 아직 검증되지 않았습니다.",
    "en": "Local execution eligibility is confirmed; successful provider execution remains unverified."
  },
  "credential.notEligible": {
    "ko": "새 모델 실행 시도 조건이 아직 확인되지 않았습니다.",
    "en": "Eligibility for a new model execution has not yet been confirmed."
  },
  "credential.modelUnverified": {
    "ko": "실제 모델 실행 성공은 아직 확인되지 않았습니다.",
    "en": "Successful model execution has not yet been verified."
  },
  "credential.providerUnverified": {
    "ko": "실제 공급자 호출은 아직 확인되지 않았습니다.",
    "en": "A live provider call has not yet been verified."
  },
  "credential.isolatedCodex": {
    "ko": "THOTH 전용 별도 프로필을 사용하며 기존 Codex Desktop 인증 파일을 가져오지 않습니다.",
    "en": "THOTH uses a separate profile and does not import existing Codex Desktop credentials."
  },
  "credential.isolated": {
    "ko": "THOTH 전용 별도 프로필을 사용합니다.",
    "en": "THOTH uses its own separate profile."
  },
  "credential.xaiIsolated": {
    "ko": "xAI 로그인은 이 THOTH 작업 공간의 별도 인증을 사용합니다. API 키 연결은 유지됩니다.",
    "en": "xAI login uses separate authentication for this THOTH workspace. API key connections are preserved."
  },
  "credential.experimental": {
    "ko": "Codex 모델 연결은 실험적입니다.",
    "en": "The Codex model connection is experimental."
  },
  "credential.claudeCodeProfile": {
    "ko": "Claude 로그인은 공식 Claude Code가 THOTH 전용 프로필에서 처리합니다.",
    "en": "Official Claude Code handles Claude login in a THOTH-specific profile."
  },
  "credential.claudeOauthChoice": {
    "ko": "Claude OAuth는 사용자가 명시적으로 선택하는 별도 경로입니다.",
    "en": "Claude OAuth is a separate route that starts only when explicitly selected."
  },
  "credential.loginThenRefresh": {
    "ko": "로그인을 완료한 뒤 연결 상태를 다시 확인하세요.",
    "en": "Finish login, then refresh the connection status."
  },
  "credential.startNotComplete": {
    "ko": "로그인 절차를 마친 뒤 연결 상태를 다시 확인하세요. 시작 응답은 연결 완료가 아닙니다.",
    "en": "Finish login, then refresh the connection status. Starting login does not confirm a connection."
  },
  "credential.checkInstall": {
    "ko": "실행 파일의 설치·지원 버전을 확인하세요. THOTH가 자동 설치하거나 기존 Desktop 인증을 변경하지 않습니다.",
    "en": "Check the executable and supported version. THOTH does not install it automatically or change existing Desktop credentials."
  },
  "credential.catalogUnverified": {
    "ko": "로그인과 별개로 사용할 모델 목록을 확인하지 못했습니다.",
    "en": "The model list could not be verified independently of login."
  },
  "credential.routeUnavailable": {
    "ko": "THOTH에서 사용할 수 있는 연결 경로를 확인하지 못했습니다.",
    "en": "No usable THOTH connection route could be verified."
  },
  "credential.noSelectableRoute": {
    "ko": "현재 선택 가능한 모델 경로가 없습니다.",
    "en": "No model route is currently selectable."
  },
  "credential.claudeCodeHint": {
    "ko": "Claude 로그인은 공식 Claude Code로 진행되며 직접 선택해야 시작됩니다. API 키 연결은 별도입니다.",
    "en": "Claude login uses official Claude Code and starts only when selected. API key connections are separate."
  },
  "credential.claudeOauthHint": {
    "ko": "Claude Code 안내와 THOTH 전용 Claude OAuth는 별도입니다. OAuth 로그인은 직접 선택해야 시작됩니다.",
    "en": "Claude Code guidance and THOTH-specific Claude OAuth are separate. OAuth login starts only when selected."
  },
  "credential.xaiReady": {
    "ko": "xAI 로그인이 확인됐습니다. 모델 목록에서 xai-oauth 경로를 직접 선택할 수 있습니다.",
    "en": "xAI login is confirmed. You can select the xai-oauth route from the model list."
  },
  "credential.xaiNoRoute": {
    "ko": "xAI 로그인은 확인됐지만 사용할 모델 경로가 아직 없습니다. 재로그인보다 모델 목록을 확인하세요.",
    "en": "xAI login is confirmed, but no model route is available yet. Check the model list before logging in again."
  },
  "credential.xaiStart": {
    "ko": "THOTH 작업 공간의 xAI 기기 코드 로그인을 시작하거나 API 키를 별도로 등록할 수 있습니다.",
    "en": "Start xAI device-code login for this workspace or register an API key separately."
  },
  "credential.codexReady": {
    "ko": "Codex 로그인은 확인됐습니다. 모델 목록이 없으면 재로그인 대신 모델 경로를 확인하세요.",
    "en": "Codex login is confirmed. If the model list is missing, check model routes before logging in again."
  },
  "credential.codexStart": {
    "ko": "THOTH 전용 Codex 로그인을 시작하거나 API 키를 등록합니다. 시작 응답만으로 로그인 완료는 아닙니다.",
    "en": "Start THOTH-specific Codex login or register an API key. Starting login does not confirm completion."
  },
  "credential.keyOnly": {
    "ko": "계정 로그인 연결은 지원되지 않습니다. API 키를 발급받아 등록하세요.",
    "en": "Account login is not supported. Obtain and register an API key."
  },
  "credential.methodsPending": {
    "ko": "서버의 로그인 지원 정보를 확인한 뒤 연결 방법을 안내합니다. API 키 등록은 별도로 할 수 있습니다.",
    "en": "Connection methods appear after login support is checked with the server. You can register an API key separately."
  },
  "credential.keySaved": {
    "ko": "API 키를 THOTH 로컬 저장소에 등록했습니다. 실제 모델 사용 가능 여부는 사용 시 확인됩니다.",
    "en": "The API key was registered in THOTH's local store. Actual model availability is checked when used."
  },
  "credential.sameWorkspace": {
    "ko": "현재 서버와 같은 workspace의 THOTH 전용 연결 방법을 확인하세요.",
    "en": "Check the THOTH-specific connection method for the workspace used by this server."
  },
  "credential.obtainKey": {
    "ko": "계정 로그인 연결은 지원되지 않습니다. 키 발급 사이트에서 API 키를 만든 뒤 THOTH에 등록하세요. 사이트 방문만으로 연결되지는 않습니다.",
    "en": "Account login is not supported. Create an API key on the provider's site and register it in THOTH. Visiting the site alone does not establish a connection."
  },
  "credential.notConfirmed": {
    "ko": "연결 완료를 확인하지 못했습니다. 연결 상태를 다시 확인하세요.",
    "en": "The connection could not be confirmed. Refresh the connection status."
  }
} as const;
