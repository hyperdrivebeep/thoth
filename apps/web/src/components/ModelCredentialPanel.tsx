import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, InputGroup } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { rpc } from "../api/rpcClient";
import { canStartCredentialMethod, credentialAccountLabel, credentialAuthMethod, credentialAvailabilityNote, credentialCompanies, credentialGuideUrl, credentialResultMessage, loginSupported, loginUnavailable,
  xaiDeviceLoginSupported, type CredentialAccount, type CredentialRegisterResult } from "./modelCredentialPresentation";
import { XaiDeviceLogin } from "./XaiDeviceLogin";
import { OAuthLoginFlow } from "./OAuthLoginFlow";

export function ModelCredentialPanel({ projectId, workspaceId }: { projectId: string; workspaceId?: string }) {
  useTranslation();
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
      <p className="eyebrow">{t("credential.connections")}</p>
      <h2>ChatGPT · Claude · xAI</h2>
      <p className="muted">{t("credential.registrationNotice")}</p>
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
        {guideUrl && <p><a href={guideUrl} target="_blank" rel="noopener noreferrer">{t("setup.openKeySite")}</a></p>}</Callout>}
      {connect.error && (
        <Callout compact intent="danger">
          {connect.error.message}
        </Callout>
      )}
      {listed.error && <Callout compact intent="warning">{t("credential.methodsFailed")}</Callout>}
      <Button small minimal icon="refresh" loading={listed.isFetching} onClick={() => void listed.refetch()}>{t("setup.refreshConnection")}</Button>
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
  const codexMethod = company.provider === "openai" ? credentialAuthMethod(knownRow, "codex_isolated_browser") : null;
  const xaiMethod = company.provider === "xai" ? credentialAuthMethod(knownRow, "xai_device_code") : null;
  const claudeCodeMethod = company.provider === "anthropic" ? credentialAuthMethod(knownRow, "claude_code_login") : null;
  const claudeMethod = company.provider === "anthropic" && !claudeCodeMethod ? credentialAuthMethod(knownRow, "claude_pkce") : null;
  const keyMethod = credentialAuthMethod(knownRow, "api_key");
  return (
    <div className="company-account">
      <p>
        {company.label} · {credentialAccountLabel(row)}
      </p>
      {credentialAvailabilityNote(row) && <small>{credentialAvailabilityNote(row)}</small>}
      {keyMethod && <small>{message("keyRouteStatus", keyMethod.connected ? t("setup.registered") : t("setup.unregistered"))}</small>}
      <div className="company-account-actions">
        {!xaiLogin && !codexMethod && !claudeCodeMethod && !claudeMethod && <Button small disabled={!canStartLogin && !canGuideKey} loading={pending} onClick={onLogin}>
          {loginUnavailable(row) ? t("setup.checkVersion") : row?.connection_state === "LOGIN_PENDING" ? t("setup.waitLogin") : row?.oauth && loginSupported(row) ? t("setup.loggedIn") : canStartLogin ? t("setup.startCodex") : canGuideKey ? t("setup.keyGuide") : t("setup.checkMethods")}
        </Button>}
        <InputGroup
          type="password"
          placeholder={t("setup.apiKey")}
          aria-label={message("apiKeyLabel", company.label)}
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
          {t("setup.registerKey")}
        </Button>
      </div>
      {xaiLogin && <XaiDeviceLogin key={`${workspaceId ?? "unknown"}:${projectId}`} projectId={projectId}
        workspaceId={workspaceId} account={row} method={xaiMethod ?? undefined}
        methodStartAllowed={row?.auth_methods ? canStartCredentialMethod(row, "xai_device_code") : undefined} />}
      {codexMethod && <OAuthLoginFlow key={`${workspaceId ?? "unknown"}:${projectId}:openai`}
        projectId={projectId} workspaceId={workspaceId} provider="openai"
        authMethod="codex_isolated_browser" method={codexMethod} account={row} />}
      {claudeCodeMethod && <OAuthLoginFlow key={`${workspaceId ?? "unknown"}:${projectId}:anthropic:claude-code`}
        projectId={projectId} workspaceId={workspaceId} provider="anthropic"
        authMethod="claude_code_login" method={claudeCodeMethod} account={row} />}
      {claudeMethod && <OAuthLoginFlow key={`${workspaceId ?? "unknown"}:${projectId}:anthropic`}
        projectId={projectId} workspaceId={workspaceId} provider="anthropic"
        authMethod="claude_pkce" method={claudeMethod} account={row} />}
    </div>
  );
}
