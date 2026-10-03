export type CredentialRegisterResult = {
  started?: boolean;
  provider?: string;
  account_provider?: string;
  auth_method?: string;
  route?: string;
  login_state?: string;
  auth_state?: string;
  catalog_state?: string;
  capabilities?: CredentialLoginCapabilities;
  authorization_url?: string;
  manual_response_required?: boolean;
  kind?: string;
  login_id?: string;
  state?: string;
  user_code?: string;
  verification_uri?: string;
  expires_at?: number;
  browser_url?: string;
  reason_code?: string;
  guidance?: string | null;
  profile_mode?: string;
  credential?: { provider?: string; model?: string };
};

export type CredentialLoginCapabilities = { start?: boolean; status?: boolean; cancel?: boolean; manual_complete?: boolean };
export type CredentialAuthMethod = {
  auth_method: string;
  route: string;
  connected?: boolean;
  execution_eligible?: boolean;
  connection_state?: string;
  reason_code?: string | null;
  login_state?: string;
  auth_state?: string;
  catalog_state?: string;
  capabilities?: CredentialLoginCapabilities;
};

export type CredentialAccount = {
  provider: string;
  label: string;
  connected: boolean;
  has_key: boolean;
  oauth: boolean;
  login_supported?: boolean;
  login_kind?: string;
  remote_auth_verified?: boolean | null;
  available_model_providers?: string[];
  connection_state?: string;
  profile_mode?: string;
  reason_code?: string | null;
  guidance?: string | null;
  execution_eligible?: boolean | null;
  execution_verified?: boolean | null;
  auth_methods?: CredentialAuthMethod[];
};

export const credentialCompanies = [
  { provider: "openai", label: "ChatGPT" },
  { provider: "anthropic", label: "Claude" },
  { provider: "xai", label: "xAI" },
] as const;

export function loginSupported(account?: CredentialAccount): boolean {
  return account?.login_supported === true && ["codex_isolated_browser", "codex_device_auth", "xai_device_code"].includes(account.login_kind ?? "");
}

export function xaiDeviceLoginSupported(account?: CredentialAccount): boolean {
  return account?.provider === "xai" && account.login_supported === true &&
    (account.login_kind === "xai_device_code" || Boolean(credentialAuthMethod(account, "xai_device_code")));
}

export function credentialAuthMethod(account: CredentialAccount | undefined, authMethod: string): CredentialAuthMethod | null {
  if (!Array.isArray(account?.auth_methods)) return null;
  const value = account.auth_methods.find(item => item && item.auth_method === authMethod);
  return value && typeof value.route === "string" && value.route.length > 0 ? value : null;
}

export function canStartCredentialMethod(account: CredentialAccount | undefined, authMethod: string): boolean {
  const method = credentialAuthMethod(account, authMethod);
  return Boolean(method && account?.login_supported === true && method.capabilities?.start === true);
}

export function loginUnavailable(account?: CredentialAccount): boolean {
  return ["CODEX_STANDALONE_NOT_INSTALLED", "CODEX_STANDALONE_PIN_UNAVAILABLE", "UPDATE_REVIEW_REQUIRED"].includes(account?.connection_state ?? "");
}

export function credentialLocallyConfigured(account?: CredentialAccount): boolean {
  if (!account?.connected) return false;
  if (["UNAVAILABLE", "LOGIN_REQUIRED", "LOGIN_PENDING", "CATALOG_UNAVAILABLE", "ERROR"].includes(account.connection_state ?? "") || loginUnavailable(account)) return false;
  if (account.execution_eligible === false || account.connection_state === "EXECUTION_UNVERIFIED" && account.execution_eligible !== true) return false;
  const routes = account.available_model_providers ?? [];
  const oauthRoute = Array.isArray(account.auth_methods) && account.auth_methods.some(method => method && method.auth_method !== "api_key" && method.connected === true
    && method.execution_eligible === true && routes.includes(method.route));
  return (account.has_key && routes.includes(account.provider)) ||
    (account.oauth && (oauthRoute || !account.auth_methods && loginSupported(account)
      && routes.includes(account.provider === "xai" ? "xai-oauth" : "codex-oauth")));
}

export function credentialAccountLabel(account?: CredentialAccount): string {
  if (account?.connection_state === "UNAVAILABLE") return "연결 경로 없음";
  if (account?.connection_state === "CODEX_STANDALONE_NOT_INSTALLED") return "Codex 실행 파일 없음";
  if (account?.connection_state === "CODEX_STANDALONE_PIN_UNAVAILABLE") return "지원 버전 없음";
  if (account?.connection_state === "UPDATE_REVIEW_REQUIRED") return "업데이트 검토 필요";
  if (account?.connection_state === "LOGIN_REQUIRED") return "로그인 필요";
  if (account?.connection_state === "LOGIN_PENDING") return "로그인 진행 중";
  if (account?.connection_state === "CATALOG_UNAVAILABLE") return "모델 목록 없음";
  if (account?.connection_state === "EXECUTION_UNVERIFIED") return account.execution_eligible === true ? "실행 시도 가능 · 성공 미검증" : "실행 미검증";
  if (account?.connection_state === "ERROR") return "연결 확인 실패";
  if (account?.connection_state === "API_KEY_AVAILABLE") return "키 등록됨 · 연결 준비";
  if (account?.connection_state === "READY") return account.has_key ? "키 등록됨 · 연결 준비" : "로그인 상태 확인됨 · 연결 준비";
  if (!account?.connected) return "아직 없음";
  if (!credentialLocallyConfigured(account) && !account.has_key) return "연결 경로 확인 필요";
  if (account.oauth && account.has_key) return "로그인 상태 확인 · 키 등록됨";
  if (account.oauth) return "로그인 상태 확인됨";
  if (account.has_key) return "키 등록됨";
  return "연결 상태 확인 필요";
}

/** The server's connection hints are English; show the Korean wording for the ones it sends and drop any it does not know. */
const guidanceKo: Record<string, string> = {
  "Use the saved THOTH workspace API key": "저장된 THOTH 작업 공간 API 키를 사용합니다.",
  "Install the pinned Codex standalone package in the THOTH tools prefix": "THOTH 도구 폴더에 지정된 버전의 Codex를 설치하세요.",
  "Connect the THOTH-only Codex profile": "THOTH 전용 Codex 프로필로 로그인하세요.",
  "Check the isolated Codex model catalog": "THOTH 전용 Codex의 모델 목록을 확인하세요.",
  "The Codex route is eligible; live execution has not been verified": "Codex 경로는 사용을 시도할 수 있지만 실제 실행은 아직 확인되지 않았습니다.",
  "Install the official Claude Code executable, then sign in from THOTH": "공식 Claude Code를 설치한 뒤 THOTH에서 로그인하세요.",
  "Sign in with the official Claude Code executable in the THOTH profile": "THOTH 전용 프로필의 공식 Claude Code로 로그인하세요.",
  "Use a THOTH workspace API key for this provider": "이 공급자는 THOTH 작업 공간 API 키로 연결하세요.",
  "Use the THOTH workspace xAI device login; remote execution is unverified": "THOTH 작업 공간의 xAI 기기 코드 로그인을 사용하세요. 실제 실행은 아직 확인되지 않았습니다.",
  "Finish the THOTH Codex sign-in, then check connection status": "THOTH 전용 Codex 로그인을 마친 뒤 연결 상태를 확인하세요.",
  "Check the isolated Codex connection and model catalog": "THOTH 전용 Codex의 연결과 모델 목록을 확인하세요.",
};
export function guidanceText(value: string | null | undefined): string | null {
  if (!value) return null;
  if (guidanceKo[value]) return guidanceKo[value];
  // A terminal command stays literal; only the sentence around it is Korean.
  const command = /^Run (.+) in the server terminal$/.exec(value);
  if (command) return "서버 터미널에서 다음 명령을 실행하세요: " + command[1];
  return /[가-힣]/.test(value) ? value : null;
}

export function credentialAvailabilityNote(account?: CredentialAccount): string | null {
  if (!account) return null;
  const notes: string[] = [];
  if (account.profile_mode === "THOTH_ISOLATED") notes.push(loginSupported(account)
    ? "THOTH 전용 별도 프로필을 사용하며 기존 Codex Desktop 인증 파일을 가져오지 않습니다."
    : "THOTH 전용 별도 프로필을 사용합니다.");
  if (account.profile_mode === "THOTH_XAI_OAUTH") notes.push("xAI 로그인은 이 THOTH 작업 공간의 별도 인증을 사용합니다. API 키 연결은 유지됩니다.");
  if (loginSupported(account) && !xaiDeviceLoginSupported(account)) notes.push("Codex 모델 연결은 실험적이며 실행 성공은 별도 확인이 필요합니다.");
  if (xaiDeviceLoginSupported(account)) notes.push("xAI 로그인 확인과 실제 모델 실행 성공은 별도입니다.");
  if (credentialAuthMethod(account, "claude_code_login")) notes.push("Claude 로그인은 공식 Claude Code가 THOTH 전용 프로필에서 처리합니다. 실제 모델 실행 성공은 첫 연구에서 확인합니다.");
  if (credentialAuthMethod(account, "claude_pkce")) notes.push("Claude OAuth는 사용자가 명시적으로 선택하는 별도 경로입니다. 실제 제3자 사용 허용과 모델 실행 성공은 아직 확인되지 않았습니다.");
  if (account.connection_state === "LOGIN_REQUIRED") notes.push("로그인을 완료한 뒤 연결 상태를 다시 확인하세요.");
  if (account.connection_state === "LOGIN_PENDING") notes.push("로그인 절차를 마친 뒤 연결 상태를 다시 확인하세요. 시작 응답은 연결 완료가 아닙니다.");
  if (loginUnavailable(account)) notes.push("실행 파일의 설치·지원 버전을 확인하세요. THOTH가 자동 설치하거나 기존 Desktop 인증을 변경하지 않습니다.");
  if (account.connection_state === "CATALOG_UNAVAILABLE") notes.push("로그인과 별개로 사용할 모델 목록을 확인하지 못했습니다.");
  if (account.connection_state === "EXECUTION_UNVERIFIED") notes.push(account.execution_eligible === true
    ? "로컬 실행 시도 조건은 확인됐지만 실제 공급자 성공은 아직 검증되지 않았습니다."
    : "새 모델 실행 시도 조건이 아직 확인되지 않았습니다.");
  if (account.connection_state === "UNAVAILABLE" && account.reason_code) notes.push(`연결 경로를 사용할 수 없습니다 (${account.reason_code}).`);
  const guidance = guidanceText(account.guidance);
  if (guidance) notes.push(guidance);
  if (!account.connected) return notes.join(" ") || null;
  if (!credentialLocallyConfigured(account) && !account.has_key) notes.push("THOTH에서 사용할 수 있는 연결 경로를 확인하지 못했습니다.");
  const routes = account.available_model_providers;
  if (routes?.length === 0) notes.push("현재 선택 가능한 모델 경로가 없습니다.");
  if (account.execution_verified === false) notes.push("실제 모델 실행 성공은 아직 확인되지 않았습니다.");
  if (account.remote_auth_verified !== true) notes.push("실제 공급자 호출은 아직 확인되지 않았습니다.");
  return notes.join(" ") || null;
}

export function credentialConnectionHint(account?: CredentialAccount): string {
  if (credentialAuthMethod(account, "claude_code_login")) return "Claude 로그인은 공식 Claude Code로 진행되며 직접 선택해야 시작됩니다. API 키 연결은 별도입니다.";
  if (credentialAuthMethod(account, "claude_pkce")) return "Claude Code 안내와 THOTH 전용 Claude OAuth는 별도입니다. OAuth 로그인은 직접 선택해야 시작됩니다.";
  if (xaiDeviceLoginSupported(account) && account?.oauth) return account.available_model_providers?.includes("xai-oauth")
    ? "xAI 로그인이 확인됐습니다. 모델 목록에서 xai-oauth 경로를 직접 선택할 수 있습니다. 실제 실행 성공은 별도 확인이 필요합니다."
    : "xAI 로그인은 확인됐지만 사용할 모델 경로가 아직 없습니다. 재로그인보다 모델 목록을 확인하세요.";
  if (xaiDeviceLoginSupported(account)) return "THOTH 작업 공간의 xAI 기기 코드 로그인을 시작하거나 API 키를 별도로 등록할 수 있습니다.";
  if (account?.oauth && loginSupported(account)) return "Codex 로그인은 확인됐습니다. 모델 목록이 없으면 재로그인 대신 모델 경로를 확인하세요.";
  if (loginSupported(account)) return "THOTH 전용 Codex 로그인을 시작하거나 API 키를 등록합니다. 시작 응답만으로 로그인 완료는 아닙니다.";
  if (account?.login_supported === false && account.login_kind === "unsupported") return "계정 로그인 연결은 지원되지 않습니다. API 키를 발급받아 등록하세요.";
  return "서버의 로그인 지원 정보를 확인한 뒤 연결 방법을 안내합니다. API 키 등록은 별도로 할 수 있습니다.";
}

export function credentialResultMessage(result?: CredentialRegisterResult): string {
  if (result?.credential) return "API 키를 THOTH 로컬 저장소에 등록했습니다. 실제 모델 사용 가능 여부는 사용 시 확인됩니다.";
  if (result?.kind === "manual_device_auth") {
    const guidance = result.guidance && !/\bcodex\s+login\b/i.test(result.guidance)
      ? guidanceText(result.guidance) ?? "현재 서버와 같은 workspace의 THOTH 전용 연결 방법을 확인하세요." : "현재 서버와 같은 workspace의 THOTH 전용 연결 방법을 확인하세요.";
    return `${guidance} 완료한 뒤 연결 상태를 다시 확인하세요. THOTH는 로그인을 시작하거나 완료하지 않았습니다.`;
  }
  if (result?.kind === "unsupported" || result?.kind === "console") return "계정 로그인 연결은 지원되지 않습니다. 키 발급 사이트에서 API 키를 만든 뒤 THOTH에 등록하세요. 사이트 방문만으로 연결되지는 않습니다.";
  if (result?.kind === "oauth" && result.started) return `${result.profile_mode === "THOTH_ISOLATED" ? "THOTH 전용 Codex" : "계정"} 로그인 절차를 시작했습니다. 완료한 뒤 연결 상태를 다시 확인하세요. ${guidanceText(result.guidance) ?? ""}`.trim();
  return "연결 완료를 확인하지 못했습니다. 연결 상태를 다시 확인하세요.";
}

export function credentialGuideUrl(result?: CredentialRegisterResult): string | null {
  const url = result?.browser_url;
  return (result?.kind === "unsupported" || result?.kind === "console") && typeof url === "string" && url.startsWith("https://") ? url : null;
}
