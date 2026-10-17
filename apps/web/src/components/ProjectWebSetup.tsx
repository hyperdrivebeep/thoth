import { t, useTranslation } from "../i18n/locale";
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

export function ProjectWebSetup({ projectId }: { projectId: string }) {
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
  const [host, setHost] = useState("");
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
  return (
    <section className="project-web-setup">
      <h2>{t("web.permittedSources")}</h2>
      <p className="muted">{t("web.registerFirst")}</p>
      {consent !== "ALLOWED" && (
        <Callout compact>{t("web.workspaceDenied")}</Callout>
      )}
      {executionState && (
        <Callout compact>
          {executionLabel(executionState.state, executionState.reason_codes)}
        </Callout>
      )}
      <InputGroup
        aria-label={t("web.domainLabel")}
        placeholder={t("web.domainExample")}
        value={host}
        onChange={(event) => setHost(event.target.value)}
        rightElement={
          <Button
            small
            disabled={!host}
            onClick={() => {
              const next = normalizePreferredHosts([...hosts, host.trim()]);
              save.mutate({ enabled, preferred_hosts: next });
              setHost("");
            }}
          >
            {t("web.addDomain")}
          </Button>
        }
      />
      <ul>
        {hosts.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <Switch
        checked={enabled}
        disabled={(!enabled && (consent !== "ALLOWED" || hosts.length === 0)) || save.isPending}
        label={t("web.useDomains")}
        onChange={(event) =>
          save.mutate({
            enabled: event.currentTarget.checked,
            preferred_hosts: normalizePreferredHosts(hosts),
          })
        }
      />
      {executionState?.state === "BLOCKED" && enabled && (
        <Button
          small
          disabled={consent !== "ALLOWED" || hosts.length === 0 || save.isPending}
          onClick={() =>
            save.mutate({ enabled: true, preferred_hosts: normalizePreferredHosts(hosts) })
          }
        >
          {t("web.reapply")}
        </Button>
      )}
      {save.error && (
        <Callout compact intent="danger">
          {save.error.message}
        </Callout>
      )}
    </section>
  );
}
