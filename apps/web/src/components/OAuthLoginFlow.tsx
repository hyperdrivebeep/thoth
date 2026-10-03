import { Button, Callout, InputGroup } from "@blueprintjs/core";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { rpc } from "../api/rpcClient";
import { CodexToolHelp } from "./CodexToolHelp";
import { ClaudeCodeHelp } from "./ClaudeCodeHelp";
import { connectionErrorText, isClaudeToolReason, isCodexToolReason, reasonCode, reasonText } from "./connectionCopy";
import { canStartCredentialMethod, type CredentialAccount, type CredentialAuthMethod, type CredentialRegisterResult } from "./modelCredentialPresentation";

type AccountProvider = "openai" | "anthropic";
type OAuthMethod = "codex_isolated_browser" | "claude_pkce" | "claude_code_login";
type LoginState = "IDLE" | "PENDING" | "CONNECTED" | "DENIED" | "EXPIRED" | "CANCELLED" | "FAILED";
type OAuthDto = CredentialRegisterResult & { login_state?: string; auth_state?: string; catalog_state?: string; catalog_refresh?: string };
type Session = { id: string; state: LoginState; authState: string; catalogState: string; catalogRefresh?: string;
  authorizationUrl: string | null; expiresAt: number; expiryEstimated: boolean; manualRequired: boolean;
  capabilities: { status: boolean; cancel: boolean; manualComplete: boolean } };

const loginStates = new Set<LoginState>(["IDLE", "PENDING", "CONNECTED", "DENIED", "EXPIRED", "CANCELLED", "FAILED"]);
const terminalStates = new Set<LoginState>(["IDLE", "CONNECTED", "DENIED", "EXPIRED", "CANCELLED", "FAILED"]);
const authStates = new Set(["DISCONNECTED", "CONNECTED", "REFRESH_REQUIRED", "REAUTH_REQUIRED", "UNKNOWN"]);
const catalogStates = new Set(["UNAVAILABLE", "AVAILABLE", "STALE"]);

function safeAuthorizationUrl(raw: unknown, provider: AccountProvider): string | null {
  if (typeof raw !== "string") return null;
  try {
    const value = new URL(raw);
    const hosts = provider === "anthropic"
      ? ["claude.ai", "claude.com"] : ["auth.openai.com", "chatgpt.com", "login.openai.com"];
    const trusted = hosts.some(host => value.hostname === host || provider === "anthropic" && value.hostname.endsWith("." + host));
    if (value.protocol !== "https:" || !trusted || value.username || value.password
      || value.port && value.port !== "443") return null;
    return value.href;
  } catch { return null; }
}

function readSession(value: OAuthDto | undefined, provider: AccountProvider, method: OAuthMethod): Session | null {
  if (value?.started !== true || value.account_provider !== provider || value.auth_method !== method
    || typeof value.login_id !== "string" || !/^[A-Za-z0-9:_-]{1,128}$/.test(value.login_id)
    || value.login_state !== "PENDING") return null;
  const reportedExpiry = typeof value.expires_at === "number" && Number.isInteger(value.expires_at)
    && value.expires_at > Date.now() / 1000 && value.expires_at <= Date.now() / 1000 + 86400
    ? value.expires_at : null;
  const expiresAt = reportedExpiry ?? Math.floor(Date.now() / 1000) + 300;
  return { id: value.login_id, state: "PENDING",
    authState: authStates.has(value.auth_state ?? "") ? value.auth_state! : "UNKNOWN",
    catalogState: catalogStates.has(value.catalog_state ?? "") ? value.catalog_state! : "UNAVAILABLE",
    authorizationUrl: safeAuthorizationUrl(value.authorization_url ?? value.browser_url, provider),
    expiresAt, expiryEstimated: reportedExpiry === null, manualRequired: value.manual_response_required === true,
    capabilities: { status: value.capabilities?.status === true, cancel: value.capabilities?.cancel === true,
      manualComplete: provider === "anthropic" && value.capabilities?.manual_complete === true } };
}

function readStatus(value: OAuthDto | undefined, provider: AccountProvider, method: OAuthMethod, id: string): Pick<Session,"state"|"authState"|"catalogState"|"catalogRefresh"> | null {
  if (value?.account_provider !== provider || value.auth_method !== method || value.login_id !== id
    || !loginStates.has(value.login_state as LoginState)) return null;
  return { state: value.login_state as LoginState,
    authState: authStates.has(value.auth_state ?? "") ? value.auth_state! : "UNKNOWN",
    catalogState: catalogStates.has(value.catalog_state ?? "") ? value.catalog_state! : "UNAVAILABLE",
    // Set when the server itself loaded (or tried to load) the model list after the sign-in finished.
    ...(["RUNNING", "DONE", "FAILED"].includes(value.catalog_refresh ?? "") ? { catalogRefresh: value.catalog_refresh } : {}) };
}

function stateCopy(state: LoginState, accountConnected = false): string {
  switch (state) {
    case "IDLE": return "이 로그인 시도를 더는 확인할 수 없습니다. 계정 상태를 다시 확인하세요.";
    case "PENDING": return accountConnected ? "로그인이 확인됐습니다. 모델 목록을 받는 중입니다." : "브라우저에서 로그인을 마쳐 주세요. 끝나면 자동으로 확인합니다.";
    case "CONNECTED": return "로그인됐습니다. 실제 모델 응답은 첫 연구에서 확인합니다.";
    case "DENIED": return "로그인이 거부됐습니다. 새 로그인은 직접 선택할 때만 시작됩니다.";
    case "EXPIRED": return "로그인 시도의 유효 시간이 끝났습니다.";
    case "CANCELLED": return "이 로그인 시도를 취소했습니다. 기존 인증과 API 키는 유지됩니다.";
    case "FAILED": return "로그인 시도에 실패했습니다. 계정 상태를 확인하세요.";
  }
}

export function OAuthLoginFlow({ projectId, workspaceId, provider, authMethod, method, account }: {
  projectId: string; workspaceId?: string; provider: AccountProvider; authMethod: OAuthMethod;
  method: CredentialAuthMethod; account?: CredentialAccount;
}) {
  const client = useQueryClient();
  const [session, setSession] = useState<Session | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualResponse, setManualResponse] = useState("");
  const [busy, setBusy] = useState<"start" | "status" | "cancel" | "complete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [cancelAttempted, setCancelAttempted] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const active = useRef(true);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const refreshedFor = useRef<string | null>(null);
  const manualInputRef = useRef<HTMLInputElement | null>(null);
  const requests = useRef<Set<AbortController>>(new Set());
  const loginId = session?.id;
  const sessionState = session?.state;
  const expired = Boolean(session && now >= session.expiresAt && session.state === "PENDING");

  useEffect(() => {
    active.current = true;
    const pending = requests.current;
    return () => { active.current = false; generation.current += 1;
      for (const request of pending) request.abort(); pending.clear(); };
  }, []);
  useEffect(() => {
    if (!loginId || !sessionState || terminalStates.has(sessionState) || expired) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [loginId, sessionState, expired]);

  const shownState = expired ? "EXPIRED" : session?.state;
  // Credentials exist: the browser step is over, so the waiting controls (timer, cancel) no longer apply.
  const accountConnected = method.connected === true || session?.authState === "CONNECTED";
  const waiting = shownState === "PENDING" && !accountConnected;
  const canStart = Boolean(workspaceId && canStartCredentialMethod(account, authMethod)
    && method.connected !== true && shownState !== "CONNECTED" && !busy && (!session || terminalStates.has(shownState!)));
  const label = provider === "openai" ? "ChatGPT" : "Claude";
  const onInstalled = () => { setError(null); setErrorCode(null); setNotice("도구를 설치했습니다. 이제 로그인을 시작할 수 있습니다."); };

  const start = async () => {
    if (!canStart || inFlight.current) return;
    inFlight.current = true;
    const ticket = ++generation.current;
    const controller = new AbortController(); requests.current.add(controller);
    if (manualInputRef.current) manualInputRef.current.value = "";
    setBusy("start"); setError(null); setErrorCode(null); setSession(null); setManualResponse(""); setManualOpen(false); setCancelAttempted(false);
    try {
      const result = await rpc<OAuthDto>("model/credential/register",
        { project_id: projectId, provider, auth_method: authMethod }, crypto.randomUUID(), controller.signal);
      if (!active.current || ticket !== generation.current) return;
      const next = readSession(result.value, provider, authMethod);
      if (!next) { setError("로그인 시작 응답의 시도 ID와 상태를 확인하지 못했습니다. 자동으로 다시 시작하지 않습니다."); return; }
      setNow(Date.now() / 1000); setSession(next); setManualOpen(next.manualRequired);
    } catch (caught) { if (active.current && ticket === generation.current) {
      setErrorCode(reasonCode(caught));
      setError(connectionErrorText(caught, "로그인 시작 결과를 확인하지 못했습니다. 새 시도는 직접 선택할 때만 시작됩니다."));
    } }
    finally { requests.current.delete(controller); inFlight.current = false;
      if (active.current && ticket === generation.current) setBusy(null); }
  };

  const status = useCallback(async () => {
    if (!session || !workspaceId || !session.capabilities.status || inFlight.current || expired) return;
    inFlight.current = true;
    const ticket = generation.current;
    const controller = new AbortController(); requests.current.add(controller);
    setBusy("status"); setError(null);
    try {
      const result = await rpc<OAuthDto>("model/credential/login/status",
        { provider, auth_method: authMethod, login_id: session.id }, crypto.randomUUID(), controller.signal);
      if (!active.current || ticket !== generation.current) return;
      const next = readStatus(result.value, provider, authMethod, session.id);
      if (!next) { setError("이 로그인 시도의 상태를 확인하지 못했습니다. 자동 조회를 멈췄습니다."); return; }
      if (terminalStates.has(next.state)) {
        if (manualInputRef.current) manualInputRef.current.value = "";
        setManualResponse(""); setManualOpen(false);
      }
      setSession(current => current?.id === session.id ? { ...current, ...next,
        ...(terminalStates.has(next.state) ? { authorizationUrl: null } : {}) } : current);
      if (next.state === "CONNECTED") {
        // One remote model-list load per finished login; polling never refreshes. When the server already
        // made its attempt, this screen only reports its outcome.
        let modelListFailed = next.catalogRefresh === "FAILED";
        if (refreshedFor.current !== session.id) {
          refreshedFor.current = session.id;
          if (next.catalogRefresh === undefined) {
            try {
              await rpc("model/catalog/refresh", { project_id: projectId }, crypto.randomUUID(), controller.signal);
            } catch { modelListFailed = true; }
          }
        }
        if (!active.current || ticket !== generation.current) return;
        const reads = await Promise.allSettled([
          client.invalidateQueries({ queryKey: ["model-credentials"] }),
          client.invalidateQueries({ queryKey: ["model-settings"] }),
          client.invalidateQueries({ queryKey: ["workspace-ready"] }),
        ]);
        if (active.current && ticket === generation.current) {
          if (modelListFailed) setError("로그인됐지만 모델 목록을 불러오지 못했습니다. 연결 상태를 확인한 뒤 다시 시도하세요.");
          else if (reads.some(read => read.status === "rejected"))
            setError("로그인 시도는 완료됐지만 계정·모델 목록을 다시 읽지 못했습니다. 연결 상태를 직접 확인하세요.");
        }
      }
    } catch { if (active.current && ticket === generation.current) setError("상태 조회에 실패했습니다. 자동 조회를 멈췄으며 새 로그인은 시작하지 않았습니다."); }
    finally { requests.current.delete(controller); inFlight.current = false;
      if (active.current && ticket === generation.current) setBusy(null); }
  }, [session, workspaceId, expired, provider, authMethod, client, projectId]);

  useEffect(() => {
    if (!session || !session.capabilities.status || shownState !== "PENDING" || !workspaceId || cancelAttempted || error || busy) return;
    const timer = window.setTimeout(() => void status(), 5000);
    return () => window.clearTimeout(timer);
  }, [session, shownState, workspaceId, cancelAttempted, error, busy, status]);

  const cancel = async () => {
    if (!session || !session.capabilities.cancel || shownState !== "PENDING" || cancelAttempted || inFlight.current) return;
    inFlight.current = true; setCancelAttempted(true); setBusy("cancel");
    if (manualInputRef.current) manualInputRef.current.value = "";
    setManualResponse(""); setManualOpen(false); setError(null);
    setSession(current => current?.id === session.id ? { ...current, authorizationUrl: null } : current);
    const ticket = generation.current;
    const controller = new AbortController(); requests.current.add(controller);
    try {
      const result = await rpc<OAuthDto>("model/credential/login/cancel",
        { provider, auth_method: authMethod, login_id: session.id }, crypto.randomUUID(), controller.signal);
      if (!active.current || ticket !== generation.current) return;
      const next = readStatus(result.value, provider, authMethod, session.id);
      if (next?.state === "CANCELLED") setSession(current => current?.id === session.id ? { ...current, ...next, authorizationUrl: null } : current);
      else setError("취소 결과를 확인하지 못했습니다. 같은 시도의 상태를 직접 확인하세요.");
    } catch { if (active.current && ticket === generation.current) setError("취소 결과를 확인하지 못했습니다. 자동으로 다시 취소하지 않습니다."); }
    finally { requests.current.delete(controller); inFlight.current = false;
      if (active.current && ticket === generation.current) setBusy(null); }
  };

  const complete = async () => {
    if (!session || provider !== "anthropic" || !session.capabilities.manualComplete || !manualResponse.trim()
      || shownState !== "PENDING" || inFlight.current) return;
    inFlight.current = true;
    const response = manualResponse.trim();
    if (manualInputRef.current) manualInputRef.current.value = "";
    setManualResponse(""); setBusy("complete"); setError(null);
    setSession(current => current?.id === session.id ? { ...current, authorizationUrl: null } : current);
    const ticket = generation.current;
    const controller = new AbortController(); requests.current.add(controller);
    try {
      const result = await rpc<OAuthDto>("model/credential/login/complete",
        { provider, auth_method: authMethod, login_id: session.id, response }, crypto.randomUUID(), controller.signal);
      if (!active.current || ticket !== generation.current) return;
      const next = readStatus(result.value, provider, authMethod, session.id);
      if (!next) { setError("수동 응답의 접수 상태를 확인하지 못했습니다. 입력값은 화면에서 지웠습니다."); return; }
      setSession(current => current?.id === session.id ? { ...current, ...next } : current);
    } catch { if (active.current && ticket === generation.current) setError("수동 응답 결과를 확인하지 못했습니다. 입력값은 화면에서 지웠습니다."); }
    finally { requests.current.delete(controller); inFlight.current = false;
      if (active.current && ticket === generation.current) setBusy(null); }
  };

  return <div className="oauth-login-flow">
    {method.capabilities?.start === true
      ? <Button intent={canStart ? "primary" : "none"} disabled={!canStart} loading={busy === "start"} onClick={() => void start()}>
        {method.connected || shownState === "CONNECTED" ? `${label} 로그인됨` : `${label}로 로그인`}</Button>
      : <p className="connection-note" role="status">{reasonText(method.reason_code) ?? "이 PC에서는 계정 로그인을 시작할 수 없습니다. API 키로 연결하세요."}</p>}
    {!workspaceId && <small role="status">작업 공간을 확인한 뒤 로그인을 시작할 수 있습니다.</small>}
    {session && <Callout compact>
      <p role="status">{stateCopy(shownState!, accountConnected)}</p>
      {session.authorizationUrl && waiting && <p><a href={session.authorizationUrl} target="_blank" rel="noopener noreferrer">로그인 페이지 열기</a></p>}
      {waiting && <p className="connection-timer" aria-live="off">{session.expiryEstimated ? "남은 상태 확인 시간" : "남은 유효 시간"}: {Math.max(0, Math.ceil(session.expiresAt - now))}초</p>}
      {waiting && <div>
        <Button small minimal disabled={busy !== null || !session.capabilities.status} onClick={() => void status()}>상태 직접 확인</Button>
        <Button small minimal disabled={cancelAttempted || busy !== null || !session.capabilities.cancel} onClick={() => void cancel()}>이 로그인 취소</Button>
      </div>}
      {provider === "anthropic" && shownState === "PENDING" && session.capabilities.manualComplete && <>
        <Button small minimal disabled={busy !== null} onClick={() => { if (manualInputRef.current) manualInputRef.current.value = ""; setManualOpen(value => !value); setManualResponse(""); }}>코드·리디렉션 URL 직접 입력</Button>
        {manualOpen && <div>
          <InputGroup type="password" inputRef={manualInputRef} aria-label="Claude 인증 응답" autoComplete="off" maxLength={4096} value={manualResponse}
            onChange={event => setManualResponse(event.target.value)} />
          <Button small disabled={!manualResponse.trim() || busy !== null} onClick={() => void complete()}>이 시도에 제출</Button>
        </div>}
      </>}
    </Callout>}
    {error && <Callout compact intent="warning" role="alert">{error}</Callout>}
    {notice && <Callout compact intent="success" role="status">{notice}</Callout>}
    {isCodexToolReason(errorCode) && <CodexToolHelp onInstalled={onInstalled} />}
    {(isClaudeToolReason(errorCode) || method.capabilities?.start !== true && isClaudeToolReason(method.reason_code)) && <ClaudeCodeHelp onInstalled={onInstalled} />}
    {(session || errorCode || account?.guidance || method.capabilities?.start !== true) && <details className="connection-tech">
      <summary>기술 정보</summary>
      <p>{method.route} · 연결 {method.connection_state ?? "미조회"} · 모델 경로 {method.execution_eligible ? "시도 가능" : "준비 전"} · 실행 성공 {account?.execution_verified === true ? "확인됨" : "미검증"}</p>
      {method.capabilities?.start !== true && <p>로그인 시작 불가{method.reason_code ? ` (${method.reason_code})` : ""}</p>}
      {session && <p>로그인 시도: {shownState} · 저장 인증: {session.authState} · 모델 목록: {session.catalogState}</p>}
      {errorCode && <p>오류 코드: {errorCode}</p>}
      {account?.guidance && <p>서버 안내 원문: {account.guidance}</p>}
    </details>}
  </div>;
}
