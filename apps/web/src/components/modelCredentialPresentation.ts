import { message as formatMessage } from "../i18n/formatMessages";
import { t, getLocale } from "../i18n/locale";
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
  if (account?.connection_state === "UNAVAILABLE") return t("credential.noRoute");
  if (account?.connection_state === "CODEX_STANDALONE_NOT_INSTALLED") return t("credential.noCodex");
  if (account?.connection_state === "CODEX_STANDALONE_PIN_UNAVAILABLE") return t("credential.noVersion");
  if (account?.connection_state === "UPDATE_REVIEW_REQUIRED") return t("credential.reviewUpdate");
  if (account?.connection_state === "LOGIN_REQUIRED") return t("credential.loginRequired");
  if (account?.connection_state === "LOGIN_PENDING") return t("credential.loginPending");
  if (account?.connection_state === "CATALOG_UNAVAILABLE") return t("credential.noCatalog");
  if (account?.connection_state === "EXECUTION_UNVERIFIED") return account.execution_eligible === true ? t("credential.canTry") : t("credential.unverified");
  if (account?.connection_state === "ERROR") return t("credential.checkFailed");
  if (account?.connection_state === "API_KEY_AVAILABLE") return t("credential.keyReady");
  if (account?.connection_state === "READY") return account.has_key ? t("credential.keyReady") : t("credential.loginReady");
  if (!account?.connected) return t("credential.none");
  if (!credentialLocallyConfigured(account) && !account.has_key) return t("credential.routeNeedsCheck");
  if (account.oauth && account.has_key) return t("credential.loginAndKey");
  if (account.oauth) return t("credential.loginConfirmed");
  if (account.has_key) return t("credential.keyRegistered");
  return t("credential.statusNeeded");
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
  if (guidanceKo[value]) return getLocale() === "en" ? value : guidanceKo[value];
  // A terminal command stays literal; only the sentence around it is Korean.
  const command = /^Run (.+) in the server terminal$/.exec(value);
  if (command) return t("labels.command") + command[1];
  return /[가-힣]/.test(value) ? value : null;
}

// The server sends two hints that are really this one statement (the real run is not confirmed yet).
const GUIDANCE_THAT_IS_THE_STATUS = new Set([
  "The Codex route is eligible; live execution has not been verified",
  "Use the THOTH workspace xAI device login; remote execution is unverified",
]);

/** The one sentence that says a real model run is not confirmed yet, or null when nothing is open. The screen
 * used to join every source (state, flags, guidance, the experimental note) so the same meaning came 5 times. */
function executionStatusNote(account: CredentialAccount): string | null {
  if (credentialAuthMethod(account, "claude_pkce")) return t("credential.thirdPartyUnverified");
  if (credentialAuthMethod(account, "claude_code_login")) return t("credential.firstRunVerify");
  if (account.connection_state === "EXECUTION_UNVERIFIED") return account.execution_eligible === true
    ? t("credential.localEligible")
    : t("credential.notEligible");
  if (!account.connected) return null;
  if (account.execution_verified === false) return t("credential.modelUnverified");
  if (account.remote_auth_verified !== true) return t("credential.providerUnverified");
  return null;
}

export function credentialAvailabilityNote(account?: CredentialAccount): string | null {
  if (!account) return null;
  const notes: string[] = [];
  if (account.profile_mode === "THOTH_ISOLATED") notes.push(loginSupported(account)
    ? t("credential.isolatedCodex")
    : t("credential.isolated"));
  if (account.profile_mode === "THOTH_XAI_OAUTH") notes.push(t("credential.xaiIsolated"));
  if (loginSupported(account) && !xaiDeviceLoginSupported(account)) notes.push(t("credential.experimental"));
  if (credentialAuthMethod(account, "claude_code_login")) notes.push(t("credential.claudeCodeProfile"));
  if (credentialAuthMethod(account, "claude_pkce")) notes.push(t("credential.claudeOauthChoice"));
  if (account.connection_state === "LOGIN_REQUIRED") notes.push(t("credential.loginThenRefresh"));
  if (account.connection_state === "LOGIN_PENDING") notes.push(t("credential.startNotComplete"));
  if (loginUnavailable(account)) notes.push(t("credential.checkInstall"));
  if (account.connection_state === "CATALOG_UNAVAILABLE") notes.push(t("credential.catalogUnverified"));
  if (account.connection_state === "UNAVAILABLE" && account.reason_code) notes.push(formatMessage("routeUnavailable", account.reason_code));
  const guidance = guidanceText(account.guidance);
  // A hint that only says the run is not confirmed is the status itself: it is shown once, in the status place.
  const guidanceIsStatus = account.guidance != null && GUIDANCE_THAT_IS_THE_STATUS.has(account.guidance);
  if (guidance && !guidanceIsStatus) notes.push(guidance);
  const status = guidanceIsStatus ? guidance : executionStatusNote(account);
  if (!account.connected) return [...notes, ...(status ? [status] : [])].join(" ") || null;
  if (!credentialLocallyConfigured(account) && !account.has_key) notes.push(t("credential.routeUnavailable"));
  const routes = account.available_model_providers;
  if (routes?.length === 0) notes.push(t("credential.noSelectableRoute"));
  if (status) notes.push(status);
  return notes.join(" ") || null;
}

export function credentialConnectionHint(account?: CredentialAccount): string {
  if (credentialAuthMethod(account, "claude_code_login")) return t("credential.claudeCodeHint");
  if (credentialAuthMethod(account, "claude_pkce")) return t("credential.claudeOauthHint");
  if (xaiDeviceLoginSupported(account) && account?.oauth) return account.available_model_providers?.includes("xai-oauth")
    ? t("credential.xaiReady")
    : t("credential.xaiNoRoute");
  if (xaiDeviceLoginSupported(account)) return t("credential.xaiStart");
  if (account?.oauth && loginSupported(account)) return t("credential.codexReady");
  if (loginSupported(account)) return t("credential.codexStart");
  if (account?.login_supported === false && account.login_kind === "unsupported") return t("credential.keyOnly");
  return t("credential.methodsPending");
}

export function credentialResultMessage(result?: CredentialRegisterResult): string {
  if (result?.credential) return t("credential.keySaved");
  if (result?.kind === "manual_device_auth") {
    const guidance = result.guidance && !/\bcodex\s+login\b/i.test(result.guidance)
      ? guidanceText(result.guidance) ?? t("credential.sameWorkspace") : t("credential.sameWorkspace");
    return formatMessage("manualConnection", guidance);
  }
  if (result?.kind === "unsupported" || result?.kind === "console") return t("credential.obtainKey");
  if (result?.kind === "oauth" && result.started) return formatMessage("loginStarted", result.profile_mode === "THOTH_ISOLATED", guidanceText(result.guidance) ?? "").trim();
  return t("credential.notConfirmed");
}

export function credentialGuideUrl(result?: CredentialRegisterResult): string | null {
  const url = result?.browser_url;
  return (result?.kind === "unsupported" || result?.kind === "console") && typeof url === "string" && url.startsWith("https://") ? url : null;
}
