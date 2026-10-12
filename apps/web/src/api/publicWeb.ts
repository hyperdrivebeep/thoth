import { t } from "../i18n/locale";
import { z } from "zod";

export const publicWebExecutionSchema = z.object({
  desired_enabled: z.boolean(),
  state: z.enum(["OFF", "READY", "BLOCKED"]),
  reason_codes: z.array(z.string()).default([]),
  managed_connector_ids: z.array(z.string()).default([]),
  effective_hosts: z.array(z.string()).default([]),
  policy_revision: z.number().nullable().optional(),
  workspace_consent: z.string().nullable().optional(),
  workspace_grant_id: z.string().nullable().optional(),
  grant_matches: z.boolean().optional(),
});

export type PublicWebExecution = z.infer<typeof publicWebExecutionSchema>;

export const publicWebUpdateInputSchema = z
  .object({
    enabled: z.boolean(),
    preferred_hosts: z.array(z.string()).default([]),
    workspace_grant_id: z.string().nullable().optional(),
  })
  .strict();

export function normalizePreferredHosts(hosts: string[]): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const raw of hosts) {
    const host = raw
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, "")
      .split("/")[0]
      ?.split(":")[0]
      ?.replace(/\.$/, "");
    if (!host || host.includes(" ") || host.includes("@") || seen.has(host)) continue;
    seen.add(host);
    normalized.push(host);
  }
  return normalized;
}

export function parsePublicWebUpdate(input: unknown) {
  const parsed = publicWebUpdateInputSchema.parse(input);
  const preferred_hosts = normalizePreferredHosts(parsed.preferred_hosts);
  if (parsed.enabled && preferred_hosts.length === 0) {
    throw new Error("PUBLIC_WEB_HOSTS_REQUIRED");
  }
  return { ...parsed, preferred_hosts };
}

export function executionLabel(state: string, reasons: string[] = []): string {
  if (state === "READY") return t("web.execution.ready");
  if (state === "OFF") return t("web.execution.off");
  if (reasons.includes("PUBLIC_WEB_POLICY_INCONSISTENT")) return t("web.execution.policy");
  if (reasons.includes("PUBLIC_WEB_GRANT_MISMATCH")) return t("web.execution.grant");
  if (reasons.includes("PUBLIC_WEB_CONNECTOR_UNAVAILABLE")) return t("web.execution.unavailable");
  if (reasons.includes("PUBLIC_WEB_CONSENT_REQUIRED")) return t("web.execution.consent");
  return t("web.execution.blocked");
}
