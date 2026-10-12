import { t } from "../i18n/locale";
export type WorkspaceSetupIssue = "CORRUPT" | "UNREADABLE" | "UNKNOWN";

export function workspaceSetupIssue(value: unknown): WorkspaceSetupIssue | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (record.setup_status === "CORRUPT" || record.reason_code === "WORKSPACE_SETUP_CORRUPT") return "CORRUPT";
  if (record.setup_status === "UNREADABLE" || record.reason_code === "WORKSPACE_SETUP_UNREADABLE") return "UNREADABLE";
  if (record.setup_status !== undefined && record.setup_status !== "MISSING") return "UNKNOWN";
  return null;
}

export function workspaceSetupIssueText(issue: WorkspaceSetupIssue): string {
  if (issue === "CORRUPT") return t("setupIssue.corrupt");
  if (issue === "UNREADABLE") return t("setupIssue.unreadable");
  return t("setupIssue.unknown");
}
