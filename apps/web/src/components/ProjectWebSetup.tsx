import { t, useTranslation } from "../i18n/locale";
import { joinSentences } from "../i18n/sentences";
import { Button, Callout, InputGroup, Switch } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { rpc } from "../api/rpcClient";
import { executionLabel, normalizePreferredHosts, publicWebExecutionSchema } from "../api/publicWeb";
import { useReadAccessProtection } from "./useReadAccessProtection";

type ProjectRead = {
  revision: number;
  policy?: { payload?: { public_web?: { enabled?: boolean; preferred_hosts?: string[] } } };
  public_web_execution?: {
    desired_enabled?: boolean;
    state?: string;
    reason_codes?: string[];
    effective_hosts?: string[];
  };
};

export function ProjectWebSetup({ projectId, onOpenConsent }: {
  projectId: string;
  /** Opens the setting that allows internet use for this workspace. */
  onOpenConsent?: () => void;
}) {
  useTranslation();
  const client = useQueryClient();
  const project = useQuery({
    queryKey: ["project-web", projectId],
    queryFn: () => rpc<ProjectRead>("project/read", { project_id: projectId }, "ui-project-web"),
  });
  const grant = useQuery({
    queryKey: ["workspace-setup"],
    queryFn: () =>
      rpc<{ internet_consent: string; internet_grant_id: string | null }>(
        "workspace/setup/read",
        {},
        "ws-setup",
      ),
  });
  const web = project.data?.value.policy?.payload?.public_web;
  const projectBlocked = useReadAccessProtection(["project-web", projectId], project.error);
  const grantBlocked = useReadAccessProtection(["workspace-setup"], grant.error);
  const hosts = web?.preferred_hosts ?? [];
  const enabled = Boolean(web?.enabled);
  const execution = publicWebExecutionSchema.safeParse(project.data?.value.public_web_execution);
  const executionState = execution.success ? execution.data : null;
  const save = useMutation({
    mutationFn: (next: { enabled: boolean; preferred_hosts: string[] }) =>
      rpc(
        "project/policy/update",
        {
          project_id: projectId,
          expected_revision: project.data?.value.revision,
          payload: {
            public_web: {
              ...next,
              workspace_grant_id: grant.data?.value.internet_grant_id ?? null,
            },
          },
        },
        crypto.randomUUID(),
      ),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["project-web", projectId] });
    },
  });
  const consent = grant.data?.value.internet_consent;
  if (projectBlocked || grantBlocked) return <Callout compact intent="warning" role="alert">{t("workspace.accessFailed")}</Callout>;
  const view: WebView = {
    hosts,
    enabled,
    stateLabel: executionState ? executionLabel(executionState.state, executionState.reason_codes) : null,
    blocked: executionState?.state === "BLOCKED",
    pending: save.isPending,
    failure: save.error ? save.error.message : null,
    onSave: next => save.mutate(next),
  };
  // Until the workspace allows the internet none of the controls can be used. Showing them switched off
  // said neither why nor where to turn them on, so that view says both and leaves them out.
  if (consent !== undefined && consent !== "ALLOWED") return <WebSetupDenied view={view} onOpenConsent={onOpenConsent} />;
  return <WebSetupControls view={view} allowed={consent === "ALLOWED"} />;
}

type WebView = {
  hosts: string[];
  enabled: boolean;
  stateLabel: string | null;
  blocked: boolean;
  pending: boolean;
  failure: string | null;
  onSave: (next: { enabled: boolean; preferred_hosts: string[] }) => void;
};

function UseDomainsSwitch({ view, canTurnOn }: { view: WebView; canTurnOn: boolean }) {
  return (
    <Switch
      checked={view.enabled}
      disabled={(!view.enabled && !canTurnOn) || view.pending}
      label={t("web.useDomains")}
      onChange={event => view.onSave({ enabled: event.currentTarget.checked, preferred_hosts: normalizePreferredHosts(view.hosts) })}
    />
  );
}

const SaveFailure = ({ view }: { view: WebView }) =>
  view.failure ? <Callout compact intent="danger" role="alert">{view.failure}</Callout> : null;

/** The workspace does not allow the internet: why nothing can be set here, and the way to the setting. */
function WebSetupDenied({ view, onOpenConsent }: { view: WebView; onOpenConsent?: () => void }) {
  return (
    <section className="project-web-setup">
      <h2>{t("web.permittedSources")}</h2>
      <p className="web-setup-denied">
        {joinSentences([t("web.workspaceDenied"), onOpenConsent && t("web.deniedNext")])}
        {onOpenConsent && <Button small minimal intent="primary" icon="cog" onClick={onOpenConsent}>{t("web.allowInSettings")}</Button>}
      </p>
      {view.hosts.length > 0 && <ul>{view.hosts.map(item => <li key={item}>{item}</li>)}</ul>}
      {/* A project that was using the web before the permission was withdrawn can still switch it off. */}
      {view.enabled && view.stateLabel && <Callout compact>{view.stateLabel}</Callout>}
      {view.enabled && <UseDomainsSwitch view={view} canTurnOn={false} />}
      <SaveFailure view={view} />
    </section>
  );
}

/** The domains this project may look up, and the switch that turns the lookup on. */
function WebSetupControls({ view, allowed }: { view: WebView; allowed: boolean }) {
  const [host, setHost] = useState("");
  const canTurnOn = allowed && view.hosts.length > 0;
  const addHost = () => {
    view.onSave({ enabled: view.enabled, preferred_hosts: normalizePreferredHosts([...view.hosts, host.trim()]) });
    setHost("");
  };
  return (
    <section className="project-web-setup">
      <h2>{t("web.permittedSources")}</h2>
      <p className="muted">{t("web.registerFirst")}</p>
      {view.stateLabel && <Callout compact>{view.stateLabel}</Callout>}
      <InputGroup
        aria-label={t("web.domainLabel")}
        placeholder={t("web.domainExample")}
        value={host}
        onChange={event => setHost(event.target.value)}
        rightElement={<Button small disabled={!host} onClick={addHost}>{t("web.addDomain")}</Button>}
      />
      <ul>
        {view.hosts.map(item => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <UseDomainsSwitch view={view} canTurnOn={canTurnOn} />
      {view.blocked && view.enabled && (
        <Button small disabled={!canTurnOn || view.pending}
          onClick={() => view.onSave({ enabled: true, preferred_hosts: normalizePreferredHosts(view.hosts) })}>
          {t("web.reapply")}
        </Button>
      )}
      <SaveFailure view={view} />
    </section>
  );
}
