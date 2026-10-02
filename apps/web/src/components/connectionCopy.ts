import { RpcError, RpcTransportError } from "../api/rpcClient";
import { credentialLocallyConfigured, type CredentialAccount } from "./modelCredentialPresentation";

/** Official pinned Codex package command, identical to docs/INSTALL.md. */
export const CODEX_TOOL_INSTALL_COMMAND =
  "npm.cmd install --prefix \"$env:LOCALAPPDATA\\THOTH\\tools\\codex\" --install-strategy=nested --save-exact @openai/codex@0.157.1";

/** Official Claude Code, pinned to the version this route was checked against. */
export const CLAUDE_CODE_INSTALL_COMMAND =
  "npm.cmd install --prefix \"$env:LOCALAPPDATA\\THOTH\\tools\\claude-code\" --install-strategy=nested --save-exact @anthropic-ai/claude-code@2.1.284";

const claudeToolReasons = new Set([
  "CLAUDE_CODE_NOT_INSTALLED", "CLAUDE_CODE_NATIVE_BINARY_REQUIRED",
  "CLAUDE_CODE_CAPABILITY_UNSUPPORTED", "CLAUDE_CODE_NOT_EXECUTABLE",
]);

const codexToolReasons = new Set([
  "CODEX_STANDALONE_NOT_INSTALLED", "CODEX_STANDALONE_PIN_UNAVAILABLE",
  "CODEX_PACKAGE_INTEGRITY_UNAVAILABLE", "CODEX_PACKAGE_INTEGRITY_MISMATCH",
  "CODEX_PACKAGE_ROOT_ABSOLUTE_REQUIRED", "UPDATE_REVIEW_REQUIRED",
]);

const reasonCopy: Record<string, string> = {
  CODEX_STANDALONE_NOT_INSTALLED: "ChatGPT 로그인에 필요한 Codex 연결 도구가 이 PC에 없습니다.",
  CODEX_STANDALONE_PIN_UNAVAILABLE: "ChatGPT 로그인에 필요한 Codex 연결 도구(0.157.1)를 찾지 못했습니다.",
  CODEX_PACKAGE_INTEGRITY_UNAVAILABLE: "Codex 연결 도구의 설치 정보를 확인하지 못했습니다. 다시 설치하세요.",
  CODEX_PACKAGE_INTEGRITY_MISMATCH: "Codex 연결 도구 파일이 공식 배포본과 다릅니다. 다시 설치하세요.",
  CODEX_PACKAGE_ROOT_ABSOLUTE_REQUIRED: "Codex 연결 도구 위치 설정이 올바르지 않습니다.",
  UPDATE_REVIEW_REQUIRED: "Codex 연결 도구의 위치나 버전이 바뀌었습니다. 다시 설치하세요.",
  CODEX_PINNED_PLATFORM_UNSUPPORTED: "이 운영체제에서는 ChatGPT 로그인을 아직 지원하지 않습니다. API 키로 연결하세요.",
  CLAUDE_CLIENT_REGISTRATION_REQUIRED: "Claude 구독 로그인은 이 PC에서 아직 켜지지 않았습니다. 지금은 API 키로 연결할 수 있습니다.",
  CLAUDE_CODE_NOT_INSTALLED: "Claude 로그인에 필요한 Claude Code가 이 PC에 없습니다.",
  CLAUDE_CODE_NATIVE_BINARY_REQUIRED: "Claude Code 실행 파일을 찾지 못했습니다. THOTH 전용 폴더에 설치하세요.",
  CLAUDE_CODE_NOT_EXECUTABLE: "Claude Code 실행 파일을 실행할 수 없습니다. 다시 설치하세요.",
  CLAUDE_CODE_CAPABILITY_UNSUPPORTED: "설치된 Claude Code 버전이 너무 오래됐습니다. THOTH 전용 폴더에 새로 설치하세요.",
  CLAUDE_CODE_LOGIN_START_FAILED: "Claude Code가 로그인을 시작하지 못했습니다. 잠시 뒤 다시 시도하세요.",
  CLAUDE_CODE_LOGIN_REQUIRED: "Claude 로그인이 필요합니다. 설정의 모델 연결에서 'Claude로 로그인'을 눌러 로그인한 뒤 다시 실행하세요.",
  CLAUDE_CODE_LOGIN_FAILED: "Claude 로그인이 끝나지 않았습니다. 새 로그인은 직접 선택할 때만 시작됩니다.",
  CLAUDE_CODE_LOGIN_NOT_CONFIRMED: "Claude Code가 종료됐지만 로그인 상태를 확인하지 못했습니다. 다시 시도하세요.",
  LOGIN_REQUIRED: "로그인이 필요합니다.",
  LOGIN_PENDING: "로그인을 마무리하는 중입니다.",
  XAI_LOGIN_REQUIRED: "xAI 로그인이 필요합니다.",
  MODEL_CREDENTIAL_UNAVAILABLE: "등록된 API 키가 없습니다.",
  NODE_NPM_UNAVAILABLE: "자동 설치에는 Node.js(npm)가 필요합니다. Node.js LTS를 설치한 뒤 다시 시도하거나 아래 명령을 직접 실행하세요.",
  CODEX_TOOL_INSTALL_FAILED: "Codex 도구 설치 명령이 실패했습니다. 인터넷 연결을 확인하고 다시 시도하세요.",
  CLAUDE_CODE_TOOL_INSTALL_FAILED: "Claude Code 설치 명령이 실패했습니다. 인터넷 연결을 확인하고 다시 시도하세요.",
  CODEX_TOOL_INSTALL_TIMEOUT: "설치가 5분 안에 끝나지 않았습니다. 잠시 뒤 다시 시도하세요.",
  CLAUDE_CODE_TOOL_INSTALL_TIMEOUT: "설치가 5분 안에 끝나지 않았습니다. 잠시 뒤 다시 시도하세요.",
  MODEL_TOOL_INSTALL_TIMEOUT: "설치가 제때 끝나지 않았습니다. 잠시 뒤 상태를 다시 확인하세요.",
  MODEL_TOOL_INSTALL_IN_PROGRESS: "이 도구를 이미 설치하는 중입니다. 끝날 때까지 기다리세요.",
  MODEL_TOOL_PREFIX_UNAVAILABLE: "THOTH 도구 폴더(LOCALAPPDATA)를 확인하지 못했습니다. 아래 명령을 직접 실행하세요.",
  CATALOG_UNAVAILABLE: "로그인은 됐지만 모델 목록을 아직 불러오지 못했습니다.",
  MODEL_CATALOG_REFRESH_TIMEOUT: "모델 목록 확인이 제때 끝나지 않았습니다. 잠시 뒤 다시 시도하세요.",
};

export function reasonCode(error: unknown): string | null {
  if (!(error instanceof RpcError)) return null;
  const match = /^([A-Z][A-Z0-9_]{2,})\b/.exec(error.message);
  return match ? match[1] : null;
}

export function isClaudeToolReason(code: string | null | undefined): boolean {
  return Boolean(code && claudeToolReasons.has(code));
}

export function isCodexToolReason(code: string | null | undefined): boolean {
  return Boolean(code && codexToolReasons.has(code));
}

export function reasonText(code: string | null | undefined): string | null {
  return code ? reasonCopy[code] ?? null : null;
}

/** Plain Korean text for a failed connection request; the original code stays in details. */
export function connectionErrorText(error: unknown, fallback: string): string {
  if (error instanceof RpcTransportError) {
    return error.kind === "TIMEOUT" ? "THOTH 서버가 제때 응답하지 않았습니다. 잠시 뒤 다시 시도하세요."
      : "THOTH 서버에 연결하지 못했습니다. 서버가 실행 중인지 확인하세요.";
  }
  return reasonText(reasonCode(error)) ?? fallback;
}

export type ProviderTone = "ready" | "pending" | "action" | "blocked";
export type ProviderStatus = { tone: ProviderTone; label: string; summary: string };

const intro: Record<string, string> = {
  openai: "ChatGPT 계정으로 로그인하거나 OpenAI API 키로 연결합니다.",
  anthropic: "Claude 계정으로 로그인하거나 Anthropic API 키로 연결합니다.",
  xai: "xAI 계정으로 로그인하거나 xAI API 키로 연결합니다.",
};

/** A finished login whose model list has not been loaded yet. */
export function needsModelRefresh(account: CredentialAccount | undefined): boolean {
  if (!account?.oauth || credentialLocallyConfigured(account)) return false;
  const state = account.connection_state ?? "";
  if (isCodexToolReason(state) || isClaudeToolReason(state) || state === "LOGIN_PENDING" || state === "ERROR") return false;
  return state === "CATALOG_UNAVAILABLE" || account.available_model_providers?.length === 0;
}

/** One status per provider card; technical states stay in the details section. */
export function providerStatus(provider: string, account: CredentialAccount | undefined, loading: boolean): ProviderStatus {
  const base = intro[provider] ?? "API 키로 연결합니다.";
  if (loading && !account) return { tone: "pending", label: "확인 중", summary: base };
  if (!account) return { tone: "action", label: "연결 안 됨", summary: base };
  if (credentialLocallyConfigured(account)) {
    const how = account.oauth && account.has_key ? "계정 로그인과 API 키로" : account.oauth ? "계정 로그인으로" : "API 키로";
    return { tone: "ready", label: "연결됨", summary: how + " 연결됐습니다. 첫 연구에서 실제 응답을 확인합니다." };
  }
  const state = account.connection_state ?? "";
  if (isCodexToolReason(state) || isClaudeToolReason(state)) return { tone: "blocked", label: "도구 설치 필요", summary: reasonText(state)! };
  if (state === "LOGIN_PENDING") return { tone: "pending", label: "로그인 중", summary: "브라우저에서 로그인을 마치면 자동으로 확인합니다." };
  if (needsModelRefresh(account))
    return { tone: "pending", label: "모델 확인 필요", summary: "로그인은 완료됐습니다. 모델 목록을 불러오면 바로 쓸 수 있습니다." };
  if (state === "ERROR") return { tone: "blocked", label: "확인 실패", summary: "연결 상태를 확인하지 못했습니다. 연결 상태를 다시 확인하세요." };
  return { tone: "action", label: "연결 안 됨", summary: base };
}

