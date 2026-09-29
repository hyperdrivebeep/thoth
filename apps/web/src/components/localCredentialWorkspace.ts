/** Only the normal LOCAL readiness projection may scope a credential login. */
export function localCredentialWorkspaceId(value: unknown): string | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const ready = value as Record<string, unknown>;
  if (Object.hasOwn(ready, "deployment_mode") && ready.deployment_mode !== "LOCAL") return undefined;
  if (ready.workspace_readable !== true) return undefined;
  const id = ready.workspace_id;
  return typeof id === "string" && /^workspace:[0-9a-f]{32}$/.test(id) ? id : undefined;
}
