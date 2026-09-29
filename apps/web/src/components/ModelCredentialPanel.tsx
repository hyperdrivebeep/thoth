import { Button, Callout, InputGroup } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { rpc } from "../api/rpcClient";
import { canStartCredentialMethod, credentialAccountLabel, credentialAuthMethod, credentialAvailabilityNote, credentialCompanies, credentialGuideUrl, credentialResultMessage, loginSupported, loginUnavailable,
  xaiDeviceLoginSupported, type CredentialAccount, type CredentialRegisterResult } from "./modelCredentialPresentation";
import { XaiDeviceLogin } from "./XaiDeviceLogin";
import { OAuthLoginFlow } from "./OAuthLoginFlow";

export function ModelCredentialPanel({ projectId, workspaceId }: { projectId: string; workspaceId?: string }) {
  const client = useQueryClient();
  const listed = useQuery({
    queryKey: ["model-credentials", projectId],
    queryFn: () =>
      rpc<{ accounts: CredentialAccount[] }>(
        "model/credential/list",
        { project_id: projectId },
        "ui-cred-list",
      ),
  });
  const connect = useMutation({
    mutationFn: (input: Record<string, string>) =>
      rpc<CredentialRegisterResult>(
        "model/credential/register",
        { project_id: projectId, ...input },
        crypto.randomUUID(),
      ),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["model-credentials", projectId] }),
        client.invalidateQueries({ queryKey: ["model-settings"] }),
      ]);
    },
  });
  const accounts = listed.error || listed.isFetching ? [] : listed.data?.value.accounts ?? [];
  const guideUrl = credentialGuideUrl(connect.data?.value);
  return (
    <section className="model-credential-panel">
      <p className="eyebrow">계정 연결</p>
      <h2>ChatGPT · Claude · xAI</h2>
      <p className="muted">지원되는 계정 로그인 절차를 시작하거나 API 키를 등록합니다. 키 발급 콘솔 방문만으로 THOTH 연결은 완료되지 않습니다.</p>
      {credentialCompanies.map((company) => {
        const row = accounts.find((item) => item.provider === company.provider);
        const knownRow = listed.data?.value.accounts.find((item) => item.provider === company.provider);
        return (
          <CompanyRow
            key={company.provider}
            company={company}
            row={row}
            knownRow={knownRow}
            xaiLogin={xaiDeviceLoginSupported(knownRow)}
            projectId={projectId}
            workspaceId={workspaceId}
            pending={connect.isPending}
            onLogin={() => connect.mutate({ provider: company.provider })}
            onKey={(api_key) => connect.mutate({ provider: company.provider, api_key })}
          />
        );
      })}
      {connect.isSuccess && <Callout compact role="status">{credentialResultMessage(connect.data?.value)}
        {guideUrl && <p><a href={guideUrl} target="_blank" rel="noopener noreferrer">키 발급 사이트 열기</a></p>}</Callout>}
      {connect.error && (
        <Callout compact intent="danger">
          {connect.error.message}
        </Callout>
      )}
      {listed.error && <Callout compact intent="warning">계정 연결 방법을 확인하지 못했습니다. 다시 확인하세요.</Callout>}
      <Button small minimal icon="refresh" loading={listed.isFetching} onClick={() => void listed.refetch()}>연결 상태 다시 확인</Button>
    </section>
  );
}

function CompanyRow({
  company,
  row,
  knownRow,
  xaiLogin,
  projectId,
  workspaceId,
  pending,
  onLogin,
  onKey,
}: {
  company: { provider: string; label: string };
  row?: CredentialAccount;
  knownRow?: CredentialAccount;
  xaiLogin: boolean;
  projectId: string;
  workspaceId?: string;
  pending: boolean;
  onLogin: () => void;
  onKey: (apiKey: string) => void;
}) {
  const [key, setKey] = useState("");
  const canStartLogin = loginSupported(row) && !row?.oauth && row?.connection_state !== "LOGIN_PENDING" && !loginUnavailable(row);
  const canGuideKey = row?.login_supported === false && row.login_kind === "unsupported";
  const canGuideClaude = company.provider === "anthropic" && Boolean(row);
  const codexMethod = company.provider === "openai" ? credentialAuthMethod(knownRow, "codex_isolated_browser") : null;
  const xaiMethod = company.provider === "xai" ? credentialAuthMethod(knownRow, "xai_device_code") : null;
  const claudeMethod = company.provider === "anthropic" ? credentialAuthMethod(knownRow, "claude_pkce") : null;
  const keyMethod = credentialAuthMethod(knownRow, "api_key");
  return (
    <div className="company-account">
      <p>
        {company.label} · {credentialAccountLabel(row)}
      </p>
      {credentialAvailabilityNote(row) && <small>{credentialAvailabilityNote(row)}</small>}
      {keyMethod && <small>API 키: {keyMethod.connected ? "등록됨" : "미등록"} · OAuth와 별도 경로</small>}
      <div className="company-account-actions">
        {!xaiLogin && !codexMethod && <Button small disabled={!canStartLogin && !canGuideKey && !canGuideClaude} loading={pending} onClick={onLogin}>
          {canGuideClaude ? "Claude Code 연결 안내" : loginUnavailable(row) ? "설치·버전 확인 필요" : row?.connection_state === "LOGIN_PENDING" ? "로그인 완료 대기" : row?.oauth && loginSupported(row) ? "로그인 확인됨" : canStartLogin ? "Codex 로그인 시작" : canGuideKey ? "키 발급 안내" : "연결 방법 확인 중"}
        </Button>}
        <InputGroup
          type="password"
          placeholder="API 키"
          aria-label={`${company.label} API 키`}
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
        <Button
          small
          intent="primary"
          disabled={!key}
          loading={pending}
          onClick={() => {
            onKey(key);
            setKey("");
          }}
        >
          키 등록
        </Button>
      </div>
      {xaiLogin && <XaiDeviceLogin key={`${workspaceId ?? "unknown"}:${projectId}`} projectId={projectId}
        workspaceId={workspaceId} account={row} method={xaiMethod ?? undefined}
        methodStartAllowed={row?.auth_methods ? canStartCredentialMethod(row, "xai_device_code") : undefined} />}
      {codexMethod && <OAuthLoginFlow key={`${workspaceId ?? "unknown"}:${projectId}:openai`}
        projectId={projectId} workspaceId={workspaceId} provider="openai"
        authMethod="codex_isolated_browser" method={codexMethod} account={row} />}
      {claudeMethod && <OAuthLoginFlow key={`${workspaceId ?? "unknown"}:${projectId}:anthropic`}
        projectId={projectId} workspaceId={workspaceId} provider="anthropic"
        authMethod="claude_pkce" method={claudeMethod} account={row} />}
    </div>
  );
}
