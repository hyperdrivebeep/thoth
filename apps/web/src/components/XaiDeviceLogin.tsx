import { Button, Callout } from "@blueprintjs/core";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { rpc } from "../api/rpcClient";
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
    case "PENDING": return "xAI 승인을 기다리고 있습니다. 로그인 시작만으로 연결된 것은 아닙니다.";
    case "SLOW_DOWN": return "xAI 승인을 기다리고 있습니다. 상태 확인 간격을 늘렸습니다.";
    case "CONNECTED": return "xAI 로그인이 확인됐습니다. 사용할 모델을 직접 선택하세요. 실제 모델 실행 성공은 별도 확인이 필요합니다.";
    case "DENIED": return "xAI 로그인이 거부됐습니다. 다시 시작하려면 새 코드를 요청하세요.";
    case "EXPIRED": return "기기 코드의 유효 시간이 끝났습니다. 다시 시작하려면 새 코드를 요청하세요.";
    case "CANCELLED": return "이 로그인 요청을 취소했습니다. 기존 API 키 연결은 유지됩니다.";
    case "FAILED": return "xAI 로그인에 실패했습니다. 연결 상태를 확인한 뒤 다시 시작하세요.";
    case "LOGIN_REQUIRED": return "현재 로그인 요청을 확인할 수 없습니다. 계정 상태를 다시 확인하세요.";
  }
}

export function XaiDeviceLogin({ projectId, workspaceId, account, method, methodStartAllowed }: {
  projectId: string; workspaceId?: string; account?: CredentialAccount; method?: CredentialAuthMethod; methodStartAllowed?: boolean;
}) {
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
      if (!next) { setError("xAI 로그인 시작 응답을 확인하지 못했습니다. 새 로그인은 자동으로 시작하지 않습니다."); return; }
      setNow(Date.now() / 1000);
      setSession(next);
    } catch {
      if (active.current && ticket === generation.current) setError("xAI 로그인 시작 결과를 확인하지 못했습니다. 계정 상태를 확인한 뒤 다시 시도하세요.");
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
      if (!next) { setError("현재 로그인 요청의 상태를 확인하지 못했습니다. 새 로그인은 자동으로 시작하지 않습니다."); return; }
      setSession((current) => current?.loginId === session.loginId ? { ...current, ...next } : current);
      if (next.state === "CONNECTED") {
        const reads = await Promise.allSettled([
          client.invalidateQueries({ queryKey: ["model-credentials"] }),
          client.invalidateQueries({ queryKey: ["model-settings"] }),
          client.invalidateQueries({ queryKey: ["workspace-ready"] }),
        ]);
        if (active.current && ticket === generation.current && reads.some(read => read.status === "rejected"))
          setError("xAI 로그인은 확인됐지만 계정·모델 목록을 다시 읽지 못했습니다. 연결 상태를 직접 확인하세요.");
      }
    } catch {
      if (active.current && ticket === generation.current && !cancelled.current) setError("로그인 상태 조회에 실패했습니다. 자동 확인을 멈췄습니다. 새 로그인은 시작하지 않았습니다.");
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
      } else setError("취소 결과를 확인하지 못했습니다. 같은 로그인 ID의 상태를 직접 확인하세요.");
    } catch {
      if (active.current && ticket === generation.current) setError("취소 결과를 확인하지 못했습니다. 자동으로 다시 취소하지 않습니다.");
    } finally {
      requests.current.delete(controller);
      cancelBusy.current = false;
      if (active.current && ticket === generation.current) setCancelling(false);
    }
  };

  return <div className="xai-device-login">
    {method && <small>{method.route} · 연결 {method.connection_state ?? "미조회"} · 모델 경로 {method.execution_eligible ? "시도 가능" : "준비 전"} · 실행 성공 {account?.execution_verified === true ? "확인됨" : "미검증"}</small>}
    <Button small disabled={!canStart} loading={starting} onClick={() => void start()}>
      {account?.oauth || shownState === "CONNECTED" ? "xAI 로그인 확인됨" : session ? "xAI 로그인 다시 시작" : "xAI 로그인 시작"}
    </Button>
    {method && method.capabilities?.start !== true && <small role="status">xAI 로그인 시작을 사용할 수 없습니다{method.reason_code ? ` (${method.reason_code})` : ""}.</small>}
    {!workspaceId && <small role="status">작업 공간을 확인한 뒤 xAI 로그인을 시작할 수 있습니다.</small>}
    {session && <Callout compact>
      <p role="status">{stateMessage(shownState!)}</p>
      <p>로그인 시도: {shownState} · 저장 인증: {session.authState} · 모델 목록: {session.catalogState}</p>
      {!terminalStates.has(shownState!) && <>
        <p>사용자 코드: <code>{session.userCode}</code></p>
        <p aria-live="off">남은 유효 시간: {Math.max(0, Math.ceil(session.expiresAt - now))}초</p>
        <a href={session.verificationUri} target="_blank" rel="noopener noreferrer">xAI 승인 페이지 열기</a>
        <div>
          <Button small minimal disabled={checking || cancelling} onClick={() => {
            // A previous status request has settled before this button is enabled again.
            // After an uncertain cancel, a deliberate read may inspect that same login ID.
            if (cancelAttempted) cancelled.current = false;
            void check();
          }}>상태 직접 확인</Button>
          <Button small minimal disabled={cancelAttempted || cancelling} loading={cancelling} onClick={() => void cancel()}>이 로그인 취소</Button>
        </div>
      </>}
    </Callout>}
    {error && <Callout compact intent="warning" role="alert">{error}</Callout>}
  </div>;
}
