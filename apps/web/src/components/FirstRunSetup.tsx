import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
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
import { WorkspaceLocationNotice } from "./WorkspaceLocationNotice";

const toneIntent: Record<ProviderTone, "success" | "primary" | "none" | "warning"> = {
  ready: "success", pending: "primary", action: "none", blocked: "warning",
};
const keyPlaceholder: Record<string, string> = { openai: "sk-...", anthropic: "sk-ant-...", xai: "xai-..." };

export function FirstRunSetup({ onDone }: { onDone: () => void }) {
  useTranslation();
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
      <p className="eyebrow">{t("setup.checkNeeded")}</p>
      <h1 id="first-run-title">{t("setup.checkWorkspace")}</h1>
      <Callout intent="warning" role="alert">{workspaceSetupIssueText(setupIssue)} {t("setup.researchIndependent")}</Callout>
      <Button small minimal icon="refresh" loading={ready.isFetching} onClick={() => void ready.refetch()}>{t("setup.checkAgain")}</Button>
    </section>
  </main>;

  return (
    <main className="first-run-shell bp6-dark">
      <section className="first-run-card" aria-labelledby="first-run-title">
        <WorkspaceLocationNotice location={value?.workspace_location} />
        <p className="eyebrow">
          {hosted ? t("setup.reviewWorkspace") : message("setupStep", currentStep === "model" ? 1 : 2)}
        </p>
        {!hosted && currentStep === "model" ? (
          <>
            <h1 id="first-run-title">{t("setup.connectModel")}</h1>
            <p className="muted">{t("setup.oneModelEnough")}</p>
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
                        {keyOpen ? t("setup.closeKey") : t("setup.useKey")}
                      </Button>
                    </header>
                    <p className="first-run-provider-summary">{status.summary}</p>
                    <div className="first-run-provider-body">
                      {needsModelRefresh(row) && <Button intent="primary" loading={loadModels.isPending}
                        onClick={() => loadModels.mutate()}>{t("setup.loadModels")}</Button>}
                      {legacyAction && <Button disabled={!canStartLogin && !canGuideKey} loading={connect.isPending} onClick={() => connect.mutate({ provider: company.provider })}>
                        {loginUnavailable(row) ? t("setup.checkVersion") : row?.connection_state === "LOGIN_PENDING" ? t("setup.waitLogin") : row?.oauth && loginSupported(row) ? t("setup.loggedIn") : canStartLogin ? t("setup.startCodex") : canGuideKey ? t("setup.keyGuide") : t("setup.checkMethods")}
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
                          placeholder={keyPlaceholder[company.provider] ?? t("setup.apiKey")}
                          value={apiKey}
                          onChange={(event) => setApiKey(event.target.value)}
                          aria-label={message("apiKeyLabel", company.label)}
                        />
                        <Button
                          intent="primary"
                          disabled={!apiKey}
                          loading={connect.isPending}
                          onClick={() => {
                            connect.mutate({ provider: company.provider, api_key: apiKey });
                          }}
                        >
                          {t("setup.registerKey")}
                        </Button>
                        <small>{t("setup.keyStorage")}</small>
                      </div>
                    )}
                    <details className="connection-tech">
                      <summary>{t("setup.details")}</summary>
                      <p>{message("connectionStatus", credentialAccountLabel(row))}</p>
                      {row?.auth_methods?.map(item => <p key={item.auth_method}>{item.route} · {item.connection_state ?? t("setup.notRead")}{item.connected ? t("setup.connectedSuffix") : ""}</p>)}
                      <p>{credentialConnectionHint(row)}</p>
                      {keyMethod && <p>{message("keyStatus", keyMethod.connected ? t("setup.registered") : t("setup.unregistered"))}</p>}
                      {availabilityNote && <p>{availabilityNote}</p>}
                    </details>
                  </article>
                );
              })}
            </div>
            <small className="muted">{t("setup.sharedUsage")}</small>
            {connect.isSuccess && <Callout compact role="status">{credentialResultMessage(connect.data?.value)}
              {guideUrl && <p><a href={guideUrl} target="_blank" rel="noopener noreferrer">{t("setup.openKeySite")}</a></p>}</Callout>}
            {loadModels.data && <Callout compact role="status" data-catalog-result>{catalogResultLines((loadModels.data.value as { catalog_status?: CatalogStatusRow[] }).catalog_status, providerLabel).map(line => <p key={line}>{line}</p>)}</Callout>}
            {loadModels.error && <Callout compact intent="warning" role="alert">{connectionErrorText(loadModels.error, t("setup.catalogFailed"))}</Callout>}
            {connect.error && <Callout compact intent="danger">{connectionErrorText(connect.error, connect.error.message)}</Callout>}
            {listed.error && <Callout compact intent="warning">{t("setup.methodsFailed")}</Callout>}
            <Button small minimal icon="refresh" loading={ready.isFetching || listed.isFetching}
              onClick={() => { void ready.refetch(); void listed.refetch(); }}>{t("setup.refreshConnection")}</Button>
            <div className="first-run-footer">
              <span>{modelConnected ? t("setup.modelReady") : t("setup.modelNeeded")}</span>
              <Button intent="primary" disabled={!modelConnected} onClick={() => setStep("internet")}>
                {t("setup.next")}
              </Button>
            </div>
          </>
        ) : (
          <>
            <h1 id="first-run-title">{t("setup.webQuestion")}</h1>
            <p className="muted">
              {hosted
                ? value?.disclosure ?? t("setup.hostedDisclosure")
                : t("setup.localDisclosure")}
            </p>
            {hosted && value?.model_connected === false && (
              <Callout compact intent="warning">{t("setup.operatorNotReady")}</Callout>
            )}
            <div className="first-run-choices">
              <Button
                fill
                alignText="left"
                icon="globe"
                className={`first-run-choice${consent === "ALLOWED" ? " selected" : ""}`}
                onClick={() => update.mutate("ALLOWED")}
              >
                <span className="first-run-choice-copy"><strong>{t("setup.allow")}</strong><span>{t("setup.allowedScope")}</span></span>
              </Button>
              <Button
                fill
                alignText="left"
                icon="disable"
                className={`first-run-choice${consent === "DENIED" ? " selected" : ""}`}
                onClick={() => update.mutate("DENIED")}
              >
                <span className="first-run-choice-copy"><strong>{t("setup.deny")}</strong><span>
                  {hosted
                    ? t("setup.hostedDeny")
                    : t("setup.localDeny")}
                </span></span>
              </Button>
            </div>
            {update.error && <Callout compact intent="danger">{update.error.message}</Callout>}
            <div className="first-run-footer">
              {hosted ? <span>{t("setup.noCredential")}</span> : <Button minimal onClick={() => setStep("model")}>{t("setup.backToModel")}</Button>}
              {setupReady ? (
                <Button intent="primary" onClick={onDone}>{t("setup.toProjects")}</Button>
              ) : (
                <span>{t("setup.chooseConsent")}</span>
              )}
            </div>
          </>
        )}
      </section>
    </main>
  );
}

