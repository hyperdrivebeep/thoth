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
  if (state === "READY") return "실행: 준비됨";
  if (state === "OFF") return "실행: 꺼짐";
  if (reasons.includes("PUBLIC_WEB_POLICY_INCONSISTENT")) return "실행: 권한 불일치";
  if (reasons.includes("PUBLIC_WEB_GRANT_MISMATCH")) return "실행: grant 불일치";
  if (reasons.includes("PUBLIC_WEB_CONNECTOR_UNAVAILABLE")) return "실행: 구현 없음";
  if (reasons.includes("PUBLIC_WEB_CONSENT_REQUIRED")) return "실행: 워크스페이스 미허용";
  return "실행: 제한됨";
}
