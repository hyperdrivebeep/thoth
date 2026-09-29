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
  if (issue === "CORRUPT") return "기존 작업 공간 설정 파일이 손상되었습니다. 신규 설정으로 취급하거나 원본을 덮어쓰지 않습니다.";
  if (issue === "UNREADABLE") return "기존 작업 공간 설정 파일을 읽지 못했습니다. 파일 접근 상태를 확인하기 전에는 새 설정을 저장하지 않습니다.";
  return "작업 공간 설정 상태를 확인하지 못했습니다. 기존 설정을 새 설정으로 대체하지 않습니다.";
}
