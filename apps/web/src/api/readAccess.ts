/** Stable error fields only; never infer permission from translated prose. */
const resourceDenials = new Set([
  "RESOURCE_ACCESS_DENIED", "RESOURCE_AUTHORITY_CHANGED", "RESOURCE_SCOPE_PROJECT_MISMATCH",
  "RESOURCE_SCOPE_UNKNOWN", "RESOURCE_REFERENCE_UNRESOLVED", "RESOURCE_LINEAGE_UNKNOWN",
]);

export function isReadAccessDenied(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const value = error as { code?: unknown; kind?: unknown; status?: unknown; details?: unknown };
  if (value.code === -32040) return true;
  if (value.kind === "HTTP" && (value.status === 401 || value.status === 403)) return true;
  const details = value.details;
  return Boolean(details && typeof details === "object" && "reason_code" in details
    && typeof details.reason_code === "string" && resourceDenials.has(details.reason_code));
}
