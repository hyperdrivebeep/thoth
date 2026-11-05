import type { MessageKey } from "../i18n/messages";
import { t } from "../i18n/locale";
import type { LanguageCode } from "../i18n/languageChoices";
import { pickLocale } from "../i18n/pickLocale";
import { connectionReason } from "../i18n/connectionReasons";
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
  return code ? connectionReason(code) ?? null : null;
}

/** Plain Korean text for a failed connection request; the original code stays in details. */
export function connectionErrorText(error: unknown, fallback: string): string {
  if (error instanceof RpcTransportError) {
    return error.kind === "TIMEOUT" ? t("connection.timeout")
      : t("connection.offline");
  }
  return reasonText(reasonCode(error)) ?? fallback;
}

export type ProviderTone = "ready" | "pending" | "action" | "blocked";
export type ProviderStatus = { tone: ProviderTone; label: string; summary: string };

const intro: Record<string, MessageKey> = {
  openai: "connection.intro.openai",
  anthropic: "connection.intro.anthropic",
  xai: "connection.intro.xai",
};

/** A finished login whose model list has not been loaded yet. */
export function needsModelRefresh(account: CredentialAccount | undefined): boolean {
  if (!account?.oauth || credentialLocallyConfigured(account)) return false;
  const state = account.connection_state ?? "";
  if (isCodexToolReason(state) || isClaudeToolReason(state) || state === "LOGIN_PENDING" || state === "ERROR") return false;
  return state === "CATALOG_UNAVAILABLE" || account.available_model_providers?.length === 0;
}

type ConnectedRoute = "BOTH" | "LOGIN" | "KEY";
const connectedSummaries: Record<LanguageCode, Record<ConnectedRoute, string>> = {
  ko: {
    BOTH: "계정 로그인과 API 키로 연결됐습니다. 첫 연구에서 실제 응답을 확인합니다.",
    LOGIN: "계정 로그인으로 연결됐습니다. 첫 연구에서 실제 응답을 확인합니다.",
    KEY: "API 키로 연결됐습니다. 첫 연구에서 실제 응답을 확인합니다.",
  },
  en: {
    BOTH: "Connected via account login and API key. Actual responses will be verified on the first research run.",
    LOGIN: "Connected via account login. Actual responses will be verified on the first research run.",
    KEY: "Connected via API key. Actual responses will be verified on the first research run.",
  },
  "zh-Hans": {
    BOTH: "已通过账户登录和 API 密钥连接。首次研究运行时会验证实际响应。",
    LOGIN: "已通过账户登录连接。首次研究运行时会验证实际响应。",
    KEY: "已通过 API 密钥连接。首次研究运行时会验证实际响应。",
  },
  ja: {
    BOTH: "アカウントのログインと API キーで接続しました。実際の応答は、最初の調査の実行時に検証されます。",
    LOGIN: "アカウントのログインで接続しました。実際の応答は、最初の調査の実行時に検証されます。",
    KEY: "API キーで接続しました。実際の応答は、最初の調査の実行時に検証されます。",
  },
};
function connectedSummary(account: CredentialAccount): string {
  return pickLocale(connectedSummaries)[account.oauth && account.has_key ? "BOTH" : account.oauth ? "LOGIN" : "KEY"];
}

/** One status per provider card; technical states stay in the details section. */
export function providerStatus(provider: string, account: CredentialAccount | undefined, loading: boolean): ProviderStatus {
  const base = t(intro[provider] ?? "connection.intro.key");
  if (loading && !account) return { tone: "pending", label: t("connection.checking"), summary: base };
  if (!account) return { tone: "action", label: t("connection.disconnected"), summary: base };
  if (credentialLocallyConfigured(account)) {
    return { tone: "ready", label: t("connection.connected"), summary: connectedSummary(account) };
  }
  const state = account.connection_state ?? "";
  if (isCodexToolReason(state) || isClaudeToolReason(state)) return { tone: "blocked", label: t("connection.toolNeeded"), summary: reasonText(state)! };
  if (state === "LOGIN_PENDING") return { tone: "pending", label: t("connection.loggingIn"), summary: t("connection.browserLogin") };
  if (needsModelRefresh(account))
    return { tone: "pending", label: t("connection.modelsNeeded"), summary: t("connection.loadAfterLogin") };
  if (state === "ERROR") return { tone: "blocked", label: t("connection.checkFailed"), summary: t("connection.refreshAdvice") };
  return { tone: "action", label: t("connection.disconnected"), summary: base };
}

