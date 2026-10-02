import { Button, Callout, InputGroup, Tag } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { rpc } from "../api/rpcClient";
import type { WorkspaceReady } from "../api/research";
import { canStartCredentialMethod, credentialAccountLabel, credentialAuthMethod, credentialAvailabilityNote, credentialCompanies, credentialConnectionHint, credentialGuideUrl, credentialLocallyConfigured, credentialResultMessage, loginSupported, loginUnavailable,
  xaiDeviceLoginSupported, type CredentialAccount, type CredentialRegisterResult } from "./modelCredentialPresentation";
import { connectionErrorText, needsModelRefresh, providerStatus, type ProviderTone } from "./connectionCopy";
import { XaiDeviceLogin } from "./XaiDeviceLogin";
import { catalogResultLines, type CatalogStatusRow } from "./modelCatalogPresentation";
import { providerLabel } from "./statusLabels";
import { OAuthLoginFlow } from "./OAuthLoginFlow";
import { localCredentialWorkspaceId } from "./localCredentialWorkspace";
import { workspaceSetupIssue, workspaceSetupIssueText } from "./workspaceSetupIssue";

const toneIntent: Record<ProviderTone, "success" | "primary" | "none" | "warning"> = {
  ready: "success", pending: "primary", action: "none", blocked: "warning",
};
const keyPlaceholder: Record<string, string> = { openai: "sk-...", anthropic: "sk-ant-...", xai: "xai-..." };

export function FirstRunSetup({ onDone }: { onDone: () => void }) {
  const client = useQueryClient();
  const ready = useQuery({
    queryKey: ["workspace-ready"],
    queryFn: () => rpc<WorkspaceReady>("workspace/ready", {}, "ws-ready"),
  });
  const setupIssue = ready.error ? null : workspaceSetupIssue(ready.data?.value);
  const listed = useQuery({
    queryKey: ["model-credentials", "system:workspace"],
    enabled: ready.isSuccess && !setupIssue && ready.data?.value.deployment_mode !== "HOSTED_REVIEW",
    queryFn: () =>
      rpc<{ accounts: CredentialAccount[] }>(
        "model/credential/list",
        { project_id: "system:workspace" },
        "ui-cred-list",
      ),
  });
  const connect = useMutation({
    mutationFn: (input: Record<string, string>) =>
      rpc<CredentialRegisterResult>(
        "model/credential/register",
        { project_id: "system:workspace", ...input },
        crypto.randomUUID(),
      ),
    onSuccess: async (result) => {
      if (result.value?.credential) { setApiKey(""); setKeyProvider(null); }
      await Promise.all([
        client.invalidateQueries({ queryKey: ["model-credentials", "system:workspace"] }),
        client.invalidateQueries({ queryKey: ["model-settings"] }),
        client.invalidateQueries({ queryKey: ["workspace-ready"] }),
      ]);
    },
  });
  const loadModels = useMutation({
    mutationFn: () => rpc("model/catalog/refresh", { project_id: "system:workspace" }, crypto.randomUUID()),
    onSettled: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["model-credentials", "system:workspace"] }),
        client.invalidateQueries({ queryKey: ["model-settings"] }),
        client.invalidateQueries({ queryKey: ["workspace-ready"] }),
      ]);
    },
  });
  const update = useMutation({
    mutationFn: (internet_consent: string) =>
      rpc("workspace/setup/update", { internet_consent }, crypto.randomUUID()),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["workspace-ready"] });
      if (hosted || modelConnected) onDone();
    },
  });
  const value = ready.error || ready.isFetching ? undefined : ready.data?.value;
  const credentialWorkspaceId = ready.error ? undefined : localCredentialWorkspaceId(ready.data?.value);
  const hosted = value?.deployment_mode === "HOSTED_REVIEW";
  const consent = value?.setup?.internet_consent ?? "UNDECIDED";
  const accounts = listed.error || listed.isFetching ? [] : listed.data?.value.accounts ?? [];
  const modelConnected = Boolean(value?.model_connected) && listed.isSuccess && accounts.some(credentialLocallyConfigured);
  const guideUrl = credentialGuideUrl(connect.data?.value);
  const setupReady = hosted ? consent !== "UNDECIDED" : modelConnected && consent !== "UNDECIDED";
  const [step, setStep] = useState<"model" | "internet">("model");
  const [keyProvider, setKeyProvider] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const currentStep = hosted ? "internet" : step;

  if (setupIssue) return <main className="first-run-shell bp6-dark">
    <section className="first-run-card" aria-labelledby="first-run-title">
      <p className="eyebrow">설정 확인 필요</p>
      <h1 id="first-run-title">기존 작업 공간 설정을 확인하세요</h1>
      <Callout intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} 저장된 연구 기록은 이 설정 파일과 별개이며 권한이 있으면 계속 읽을 수 있습니다.</Callout>
      <Button small minimal icon="refresh" loading={ready.isFetching} onClick={() => void ready.refetch()}>설정 상태 다시 확인</Button>
    </section>
  </main>;

  return (
    <main className="first-run-shell bp6-dark">
      <section className="first-run-card" aria-labelledby="first-run-title">
        <p className="eyebrow">
          {hosted ? "심사 워크스페이스" : `처음 설정 · ${currentStep === "model" ? "1 / 2" : "2 / 2"}`}
        </p>
        {!hosted && currentStep === "model" ? (
          <>
            <h1 id="first-run-title">연구를 맡길 모델을 연결하세요</h1>
            <p className="muted">하나만 있으면 됩니다. 나머지는 나중에 설정에서 추가할 수 있습니다.</p>
            <div className="first-run-providers">
              {credentialCompanies.map((company) => {
                const row = accounts.find((item) => item.provider === company.provider);
                const knownRow = listed.data?.value.accounts.find((item) => item.provider === company.provider);
                const xaiLogin = xaiDeviceLoginSupported(knownRow);
                const xaiMethod = company.provider === "xai" ? credentialAuthMethod(knownRow, "xai_device_code") : null;
                const codexMethod = company.provider === "openai" ? credentialAuthMethod(knownRow, "codex_isolated_browser") : null;
                const claudeCodeMethod = company.provider === "anthropic" ? credentialAuthMethod(knownRow, "claude_code_login") : null;
                const claudeMethod = company.provider === "anthropic" && !claudeCodeMethod ? credentialAuthMethod(knownRow, "claude_pkce") : null;
                const keyMethod = credentialAuthMethod(knownRow, "api_key");
                const canStartLogin = loginSupported(row) && !row?.oauth && row?.connection_state !== "LOGIN_PENDING" && !loginUnavailable(row);
                const canGuideKey = row?.login_supported === false && row.login_kind === "unsupported";
                const availabilityNote = credentialAvailabilityNote(row);
                const keyOpen = keyProvider === company.provider;
                const status = providerStatus(company.provider, row, listed.isFetching);
                const legacyAction = !xaiLogin && !codexMethod && !claudeMethod && !claudeCodeMethod;
                return (
                  <article className={`first-run-provider tone-${status.tone}`} key={company.provider}>
                    <header className="first-run-provider-head">
                      <span className="provider-mark" aria-hidden="true">{company.label.slice(0, 1)}</span>
                      <strong>{company.label}</strong>
                      <Tag minimal round intent={toneIntent[status.tone]}>{status.label}</Tag>
                      <Button className="provider-key-toggle" small minimal icon="key" aria-expanded={keyOpen}
                        onClick={() => { setKeyProvider(keyOpen ? null : company.provider); setApiKey(""); }}>
                        {keyOpen ? "키 입력 닫기" : "API 키로 연결"}
                      </Button>
                    </header>
                    <p className="first-run-provider-summary">{status.summary}</p>
                    <div className="first-run-provider-body">
                      {needsModelRefresh(row) && <Button intent="primary" loading={loadModels.isPending}
                        onClick={() => loadModels.mutate()}>모델 목록 불러오기</Button>}
                      {legacyAction && <Button disabled={!canStartLogin && !canGuideKey} loading={connect.isPending} onClick={() => connect.mutate({ provider: company.provider })}>
                        {loginUnavailable(row) ? "설치·버전 확인 필요" : row?.connection_state === "LOGIN_PENDING" ? "로그인 완료 대기" : row?.oauth && loginSupported(row) ? "로그인 확인됨" : canStartLogin ? "Codex 로그인 시작" : canGuideKey ? "키 발급 안내" : "연결 방법 확인 중"}
                      </Button>}
                      {xaiLogin && <XaiDeviceLogin key={`${credentialWorkspaceId ?? "unknown"}:system:workspace`}
                        projectId="system:workspace" workspaceId={credentialWorkspaceId} account={row} method={xaiMethod ?? undefined}
                        methodStartAllowed={row?.auth_methods ? canStartCredentialMethod(row, "xai_device_code") : undefined} />}
                      {codexMethod && <OAuthLoginFlow key={`${credentialWorkspaceId ?? "unknown"}:openai`}
                        projectId="system:workspace" workspaceId={credentialWorkspaceId} provider="openai"
                        authMethod="codex_isolated_browser" method={codexMethod} account={row} />}
                      {claudeCodeMethod && <OAuthLoginFlow key={`${credentialWorkspaceId ?? "unknown"}:anthropic:claude-code`}
                        projectId="system:workspace" workspaceId={credentialWorkspaceId} provider="anthropic"
                        authMethod="claude_code_login" method={claudeCodeMethod} account={row} />}
                      {claudeMethod && <OAuthLoginFlow key={`${credentialWorkspaceId ?? "unknown"}:anthropic`}
                        projectId="system:workspace" workspaceId={credentialWorkspaceId} provider="anthropic"
                        authMethod="claude_pkce" method={claudeMethod} account={row} />}
                    </div>
                    {keyOpen && (
                      <div className="first-run-key">
                        <InputGroup
                          type="password"
                          placeholder={keyPlaceholder[company.provider] ?? "API 키"}
                          value={apiKey}
                          onChange={(event) => setApiKey(event.target.value)}
                          aria-label={`${company.label} API 키`}
                        />
                        <Button
                          intent="primary"
                          disabled={!apiKey}
                          loading={connect.isPending}
                          onClick={() => {
                            connect.mutate({ provider: company.provider, api_key: apiKey });
                          }}
                        >
                          키 등록
                        </Button>
                        <small>키는 이 PC의 THOTH 작업 공간에만 저장됩니다. 사용량은 해당 공급자 계정에 청구됩니다.</small>
                      </div>
                    )}
                    <details className="connection-tech">
                      <summary>상태 자세히</summary>
                      <p>상태: {credentialAccountLabel(row)}</p>
                      {row?.auth_methods?.map(item => <p key={item.auth_method}>{item.route} · {item.connection_state ?? "미조회"}{item.connected ? " · 연결됨" : ""}</p>)}
                      <p>{credentialConnectionHint(row)}</p>
                      {keyMethod && <p>API 키: {keyMethod.connected ? "등록됨" : "미등록"} · 계정 로그인과 별도 경로</p>}
                      {availabilityNote && <p>{availabilityNote}</p>}
                    </details>
                  </article>
                );
              })}
            </div>
            <small className="muted">ChatGPT·Claude 계정 로그인은 같은 계정의 Codex·Claude Code 사용량 한도를 함께 씁니다.</small>
            {connect.isSuccess && <Callout compact role="status">{credentialResultMessage(connect.data?.value)}
              {guideUrl && <p><a href={guideUrl} target="_blank" rel="noopener noreferrer">키 발급 사이트 열기</a></p>}</Callout>}
            {loadModels.data && <Callout compact role="status" data-catalog-result>{catalogResultLines((loadModels.data.value as { catalog_status?: CatalogStatusRow[] }).catalog_status, providerLabel).map(line => <p key={line}>{line}</p>)}</Callout>}
            {loadModels.error && <Callout compact intent="warning" role="alert">{connectionErrorText(loadModels.error, "모델 목록을 불러오지 못했습니다. 잠시 뒤 다시 시도하세요.")}</Callout>}
            {connect.error && <Callout compact intent="danger">{connectionErrorText(connect.error, connect.error.message)}</Callout>}
            {listed.error && <Callout compact intent="warning">계정 연결 방법을 확인하지 못했습니다. 연결 상태를 다시 확인하세요.</Callout>}
            <Button small minimal icon="refresh" loading={ready.isFetching || listed.isFetching}
              onClick={() => { void ready.refetch(); void listed.refetch(); }}>연결 상태 다시 확인</Button>
            <div className="first-run-footer">
              <span>{modelConnected ? "연결됐습니다. 다음으로 인터넷 사용 여부를 고릅니다." : "모델을 하나 연결하면 다음으로 넘어갈 수 있습니다."}</span>
              <Button intent="primary" disabled={!modelConnected} onClick={() => setStep("internet")}>
                다음
              </Button>
            </div>
          </>
        ) : (
          <>
            <h1 id="first-run-title">공개 웹을 이 워크스페이스에서 쓸까요?</h1>
            <p className="muted">
              {hosted
                ? value?.disclosure ?? "질문과 분석에 쓰인 자료는 운영자 OpenAI API로 전달됩니다. 공개 웹 허용은 자료 조회만 해당하고, 모델 호출과는 별개입니다."
                : "모델 호출과 별개입니다. 지금 허용해도 프로젝트가 자동으로 켜지지 않습니다. 나중에 그 프로젝트에서 사이트를 등록한 뒤에만 웹을 찾습니다."}
            </p>
            {hosted && value?.model_connected === false && (
              <Callout compact intent="warning">운영자 모델이 아직 준비되지 않았습니다. 키를 입력하지 마세요.</Callout>
            )}
            <div className="first-run-choices">
              <Button
                fill
                alignText="left"
                icon="globe"
                className={`first-run-choice${consent === "ALLOWED" ? " selected" : ""}`}
                onClick={() => update.mutate("ALLOWED")}
              >
                <span className="first-run-choice-copy"><strong>허용</strong><span>연결한 파일 다음에, 프로젝트에서 등록한 사이트만 찾습니다. 지금 허용해도 자동 검색은 없습니다.</span></span>
              </Button>
              <Button
                fill
                alignText="left"
                icon="disable"
                className={`first-run-choice${consent === "DENIED" ? " selected" : ""}`}
                onClick={() => update.mutate("DENIED")}
              >
                <span className="first-run-choice-copy"><strong>거부</strong><span>
                  {hosted
                    ? "웹 자료 조회만 거부합니다. 질문과 분석은 계속 운영자 모델로 전달됩니다."
                    : "웹 자료 조회만 거부합니다. 모델 호출은 계속됩니다."}
                </span></span>
              </Button>
            </div>
            {update.error && <Callout compact intent="danger">{update.error.message}</Callout>}
            <div className="first-run-footer">
              {hosted ? <span>로그인이나 API 키는 넣지 않습니다.</span> : <Button minimal onClick={() => setStep("model")}>모델로 돌아가기</Button>}
              {setupReady ? (
                <Button intent="primary" onClick={onDone}>프로젝트로</Button>
              ) : (
                <span>허용 또는 거부를 고르면 프로젝트로 갑니다.</span>
              )}
            </div>
          </>
        )}
      </section>
    </main>
  );
}

