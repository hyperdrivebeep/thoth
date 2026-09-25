export type CredentialRegisterResult = {
  started?: boolean;
  kind?: string;
  browser_url?: string;
  reason_code?: string;
  credential?: { provider?: string; model?: string };
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
};

export const credentialCompanies = [
  { provider: "openai", label: "ChatGPT" },
  { provider: "anthropic", label: "Claude" },
  { provider: "xai", label: "xAI" },
] as const;

export function loginSupported(account?: CredentialAccount): boolean {
  return account?.login_supported === true && account.login_kind === "codex_device_auth";
}

export function credentialLocallyConfigured(account?: CredentialAccount): boolean {
  if (!account?.connected) return false;
  const routes = account.available_model_providers ?? [];
  return (account.has_key && routes.includes(account.provider)) ||
    (account.oauth && loginSupported(account) && routes.includes("codex-oauth"));
}

export function credentialAccountLabel(account?: CredentialAccount): string {
  if (!account?.connected) return "아직 없음";
  if (!credentialLocallyConfigured(account) && !account.has_key) return "연결 경로 확인 필요";
  if (account.oauth && account.has_key) return "로그인 상태 확인 · 키 등록됨";
  if (account.oauth) return "로그인 상태 확인됨";
  if (account.has_key) return "키 등록됨";
  return "연결 상태 확인 필요";
}

export function credentialAvailabilityNote(account?: CredentialAccount): string | null {
  if (!account?.connected) return null;
  if (!credentialLocallyConfigured(account) && !account.has_key) return "THOTH에서 사용할 수 있는 연결 경로를 확인하지 못했습니다.";
  const routes = account.available_model_providers;
  if (routes?.length === 0) return "현재 선택 가능한 모델 경로가 없습니다.";
  return account.remote_auth_verified === true ? null : "실제 공급자 호출은 아직 확인되지 않았습니다.";
}

export function credentialConnectionHint(account?: CredentialAccount): string {
  if (account?.oauth && loginSupported(account)) return "Codex 로그인은 확인됐습니다. 모델 목록이 없으면 재로그인 대신 모델 경로를 확인하세요.";
  if (loginSupported(account)) return "Codex 기기 로그인을 시작하거나 API 키를 등록합니다. 시작 응답만으로 로그인 완료는 아닙니다.";
  if (account?.login_supported === false && account.login_kind === "unsupported") return "계정 로그인 연결은 지원되지 않습니다. API 키를 발급받아 등록하세요.";
  return "서버의 로그인 지원 정보를 확인한 뒤 연결 방법을 안내합니다. API 키 등록은 별도로 할 수 있습니다.";
}

export function credentialResultMessage(result?: CredentialRegisterResult): string {
  if (result?.credential) return "API 키를 THOTH 로컬 저장소에 등록했습니다. 실제 모델 사용 가능 여부는 사용 시 확인됩니다.";
  if (result?.kind === "manual_device_auth") return "이 환경의 기기 로그인은 서버 터미널에서 codex login --device-auth를 직접 실행해야 합니다. 완료한 뒤 연결 상태를 다시 확인하세요. THOTH는 로그인을 시작하거나 완료하지 않았습니다.";
  if (result?.kind === "unsupported" || result?.kind === "console") return "계정 로그인 연결은 지원되지 않습니다. 키 발급 사이트에서 API 키를 만든 뒤 THOTH에 등록하세요. 사이트 방문만으로 연결되지는 않습니다.";
  if (result?.kind === "oauth" && result.started) return "계정 로그인 절차를 시작했습니다. 완료한 뒤 연결 상태를 다시 확인하세요.";
  return "연결 완료를 확인하지 못했습니다. 연결 상태를 다시 확인하세요.";
}

export function credentialGuideUrl(result?: CredentialRegisterResult): string | null {
  const url = result?.browser_url;
  return (result?.kind === "unsupported" || result?.kind === "console") && typeof url === "string" && url.startsWith("https://") ? url : null;
}
