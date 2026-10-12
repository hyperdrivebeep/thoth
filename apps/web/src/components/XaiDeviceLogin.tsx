import { message } from "../i18n/formatMessages";
import { t, useTranslation, messageKey, uiText } from "../i18n/locale";
import { Button, Callout } from "@blueprintjs/core";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { rpc } from "../api/rpcClient";
import { connectionErrorText, reasonText } from "./connectionCopy";
import type { CredentialAccount, CredentialAuthMethod, CredentialRegisterResult } from "./modelCredentialPresentation";

type LoginState = "PENDING" | "SLOW_DOWN" | "CONNECTED" | "DENIED" | "EXPIRED" | "CANCELLED" | "FAILED" | "LOGIN_REQUIRED";
type Session = { loginId: string; userCode: string; verificationUri: string; expiresAt: number; state: LoginState;
  authState: string; catalogState: string };
type LoginStatus = { provider?: string; account_provider?: string; auth_method?: string; kind?: string; login_id?: string;
  state?: string; login_state?: string; auth_state?: string; catalog_state?: string; reason_code?: string | null };

const terminalStates = new Set<LoginState>(["CONNECTED", "DENIED", "EXPIRED", "CANCELLED", "FAILED", "LOGIN_REQUIRED"]);
const loginStates = new Set<LoginState>(["PENDING", "SLOW_DOWN", ...terminalStates]);
const verificationHosts = new Set(["x.ai", "auth.x.ai", "grok.com", "accounts.x.ai"]);

function trustedVerificationUri(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || !verificationHosts.has(url.hostname) || url.username || url.password
      || url.port && url.port !== "443" || url.search || url.hash) return null;
    return url.href;
  } catch {
    return null;
  }
}

function startSession(value: CredentialRegisterResult | undefined): Session | null {
  const now = Date.now() / 1000;
  const uri = trustedVerificationUri(value?.verification_uri);
  if (value?.started !== true || value.provider !== "xai" || value.kind !== "xai_device_code"
    || typeof value.login_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(value.login_id)
    || typeof value.user_code !== "string" || value.user_code.length === 0 || value.user_code.length > 64
    || [...value.user_code].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
    || !uri || typeof value.expires_at !== "number" || !Number.isFinite(value.expires_at)
    || value.expires_at <= now || value.expires_at > now + 86400) return null;
  const state = value.login_state ?? value.state ?? "PENDING";
  if (state !== "PENDING" && state !== "SLOW_DOWN") return null;
  return { loginId: value.login_id, userCode: value.user_code, verificationUri: uri, expiresAt: value.expires_at,
    state, authState: value.auth_state ?? "UNKNOWN", catalogState: value.catalog_state ?? "UNAVAILABLE" };
}

function statusState(value: LoginStatus | undefined, loginId: string): Pick<Session,"state"|"authState"|"catalogState"> | null {
  if (value?.provider !== "xai" || value.kind !== "xai_device_code"
    || value.auth_method && value.auth_method !== "xai_device_code"
    || value.account_provider && value.account_provider !== "xai") return null;
  const state = value.login_state ?? value.state;
  if (!loginStates.has(state as LoginState)) return null;
  // A process restart can lose the pending login ID and report LOGIN_REQUIRED.
  // Only that non-success state may be accepted without the original ID.
  if (value.login_id !== loginId && !(state === "LOGIN_REQUIRED" && !value.login_id)) return null;
  return { state: state as LoginState, authState: value.auth_state ?? "UNKNOWN", catalogState: value.catalog_state ?? "UNAVAILABLE" };
}

function stateMessage(state: LoginState): string {
  switch (state) {
    case "PENDING": return t("xai.pending");
    case "SLOW_DOWN": return t("xai.slowDown");
    case "CONNECTED": return t("xai.connected");
    case "DENIED": return t("xai.denied");
    case "EXPIRED": return t("xai.expired");
    case "CANCELLED": return t("xai.cancelled");
    case "FAILED": return t("xai.failed");
    case "LOGIN_REQUIRED": return t("xai.requestUnknown");
  }
}

export function XaiDeviceLogin({ projectId, workspaceId, account, method, methodStartAllowed }: {
  projectId: string; workspaceId?: string; account?: CredentialAccount; method?: CredentialAuthMethod; methodStartAllowed?: boolean;
}) {
  useTranslation();
  const client = useQueryClient();
  const [session, setSession] = useState<Session | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [starting, setStarting] = useState(false);
  const [checking, setChecking] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelAttempted, setCancelAttempted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true);
  const generation = useRef(0);
  const startBusy = useRef(false);
  const checkBusy = useRef(false);
  const cancelBusy = useRef(false);
  const cancelled = useRef(false);
  const requests = useRef<Set<AbortController>>(new Set());
  const loginId = session?.loginId;
  const loginState = session?.state;
  const expired = Boolean(session && now >= session.expiresAt && !terminalStates.has(session.state));

  useEffect(() => {
    active.current = true;
    const pending = requests.current;
    return () => {
      active.current = false;
      generation.current += 1;
      for (const request of pending) request.abort();
      pending.clear();
    };
  }, []);
  useEffect(() => {
    if (!loginId || !loginState || terminalStates.has(loginState) || expired) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [loginId, loginState, expired]);

  const shownState = expired ? "EXPIRED" : session?.state;
  const canStart = Boolean(workspaceId && (methodStartAllowed ?? (account?.login_supported && account.login_kind === "xai_device_code"))
    && !account?.oauth && shownState !== "CONNECTED" && !starting && (!session || terminalStates.has(shownState!) || cancelAttempted && error));

  const start = async () => {
    if (!canStart || startBusy.current) return;
    startBusy.current = true;
    const ticket = ++generation.current;
    const controller = new AbortController();
    requests.current.add(controller);
    setStarting(true); setError(null); setSession(null); setCancelAttempted(false); cancelled.current = false;
    try {
      const response = await rpc<CredentialRegisterResult>("model/credential/register",
        { project_id: projectId, provider: "xai", auth_method: "xai_device_code", api_key: "" }, crypto.randomUUID(), controller.signal);
      if (!active.current || ticket !== generation.current) return;
      const next = startSession(response.value);
      if (!next) { setError(messageKey("xai.invalidStart")); return; }
      setNow(Date.now() / 1000);
      setSession(next);
    } catch (caught) {
      if (active.current && ticket === generation.current) setError(connectionErrorText(caught, t("xai.startUnknown")));
    } finally {
      requests.current.delete(controller);
      if (active.current && ticket === generation.current) { startBusy.current = false; setStarting(false); }
    }
  };

  const check = useCallback(async () => {
    if (!session || !workspaceId || checkBusy.current || Date.now() / 1000 >= session.expiresAt) return;
    checkBusy.current = true;
    const ticket = generation.current;
    const controller = new AbortController();
    requests.current.add(controller);
    setChecking(true); setError(null);
    try {
      const response = await rpc<LoginStatus>("model/credential/login/status",
        { provider: "xai", auth_method: "xai_device_code", login_id: session.loginId }, crypto.randomUUID(), controller.signal);
      if (!active.current || ticket !== generation.current || cancelled.current || Date.now() / 1000 >= session.expiresAt) return;
      const next = statusState(response.value, session.loginId);
      if (!next) { setError(messageKey("xai.statusUnknown")); return; }
      setSession((current) => current?.loginId === session.loginId ? { ...current, ...next } : current);
      if (next.state === "CONNECTED") {
        const reads = await Promise.allSettled([
          client.invalidateQueries({ queryKey: ["model-credentials"] }),
          client.invalidateQueries({ queryKey: ["model-settings"] }),
          client.invalidateQueries({ queryKey: ["workspace-ready"] }),
        ]);
        if (active.current && ticket === generation.current && reads.some(read => read.status === "rejected"))
          setError(messageKey("xai.refreshFailed"));
      }
    } catch {
      if (active.current && ticket === generation.current && !cancelled.current) setError(messageKey("xai.pollFailed"));
    } finally {
      requests.current.delete(controller);
      checkBusy.current = false;
      if (active.current && ticket === generation.current) setChecking(false);
    }
  }, [client, session, workspaceId]);

  useEffect(() => {
    if (!session || !workspaceId || expired || terminalStates.has(session.state) || cancelAttempted || error) return;
    const timer = window.setTimeout(() => void check(), session.state === "SLOW_DOWN" ? 8000 : 5000);
    return () => window.clearTimeout(timer);
  }, [session, workspaceId, expired, cancelAttempted, error, check]);

  const cancel = async () => {
    if (!session || terminalStates.has(session.state) || cancelBusy.current || cancelAttempted) return;
    cancelBusy.current = true; cancelled.current = true;
    setCancelAttempted(true); setCancelling(true); setError(null);
    const ticket = generation.current;
    const controller = new AbortController();
    requests.current.add(controller);
    try {
      const response = await rpc<LoginStatus>("model/credential/login/cancel",
        { provider: "xai", auth_method: "xai_device_code", login_id: session.loginId }, crypto.randomUUID(), controller.signal);
      if (!active.current || ticket !== generation.current) return;
      const next = statusState(response.value, session.loginId);
      if (next?.state === "CANCELLED") {
        setSession((current) => current?.loginId === session.loginId ? { ...current, ...next } : current);
      } else setError(messageKey("xai.cancelUnknown"));
    } catch {
      if (active.current && ticket === generation.current) setError(messageKey("oauth.cancelNotRetried"));
    } finally {
      requests.current.delete(controller);
      cancelBusy.current = false;
      if (active.current && ticket === generation.current) setCancelling(false);
    }
  };

  return <div className="xai-device-login">
    {method && method.capabilities?.start !== true
      ? <p className="connection-note" role="status">{reasonText(method.reason_code) ?? t("xai.unsupported")}</p>
      : <Button intent={canStart ? "primary" : "none"} disabled={!canStart} loading={starting} onClick={() => void start()}>
        {account?.oauth || shownState === "CONNECTED" ? t("xai.loggedIn") : session ? t("xai.restart") : t("xai.login")}
      </Button>}
    {!workspaceId && <small role="status">{t("xai.workspaceNeeded")}</small>}
    {session && <Callout compact>
      <p role="status">{stateMessage(shownState!)}</p>
      {!terminalStates.has(shownState!) && <>
        <p className="device-code-row">{t("labels.userCode")}{" "}<code className="device-code">{session.userCode}</code></p>
        <p><a href={session.verificationUri} target="_blank" rel="noopener noreferrer">{t("xai.openApproval")}</a>{" "}{t("xai.enterCode")}</p>
        <p className="connection-timer" aria-live="off">{message("expiry", Math.max(0, Math.ceil(session.expiresAt - now)), false)}</p>
        <div>
          <Button small minimal disabled={checking || cancelling} onClick={() => {
            // A previous status request has settled before this button is enabled again.
            // After an uncertain cancel, a deliberate read may inspect that same login ID.
            if (cancelAttempted) cancelled.current = false;
            void check();
          }}>{t("oauth.checkManually")}</Button>
          <Button small minimal disabled={cancelAttempted || cancelling} loading={cancelling} onClick={() => void cancel()}>{t("oauth.cancel")}</Button>
        </div>
      </>}
    </Callout>}
    {error && <Callout compact intent="warning" role="alert">{uiText(error)}</Callout>}
    {(session || method && method.capabilities?.start !== true) && <details className="connection-tech">
      <summary>{t("oauth.technical")}</summary>
      {method && <p>{method.route}{" "}{t("labels.connection")}{" "}{method.connection_state ?? t("setup.notRead")}{" "}{t("labels.modelRoute")}{" "}{method.execution_eligible ? t("oauth.canAttempt") : t("oauth.notReady")}{" "}{t("labels.executionSuccess")}{" "}{account?.execution_verified === true ? t("oauth.verified") : t("oauth.unverified")}</p>}
      {method && method.capabilities?.start !== true && <p>{t("labels.loginUnavailable")}{method.reason_code ? ` (${method.reason_code})` : ""}</p>}
      {session && <p>{t("labels.loginAttempt")}{" "}{shownState}{" "}{t("labels.storedAuth")}{" "}{session.authState}{" "}{t("labels.modelList")}{" "}{session.catalogState}</p>}
    </details>}
  </div>;
}
