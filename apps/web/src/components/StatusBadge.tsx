import { Tag } from "@blueprintjs/core";

export function StatusBadge({ value }: { value: string | null | undefined }) {
  const recorded = typeof value === "string" && value.trim() ? value : null;
  const normalized = recorded?.toLowerCase().replaceAll("_", "-") ?? "unrecorded";
  const intent = recorded && /HOLD|PARTIAL|PENDING|UNKNOWN|REQUIRED|STALE/.test(recorded) ? "warning" : recorded && /FAIL|INVALID|CANCEL/.test(recorded) ? "danger" : recorded && /SUCCEEDED|CURRENT|PASS|VALID/.test(recorded) ? "success" : "none";
  return <Tag minimal intent={intent} className={`badge badge-${normalized}`}>{recorded?.replaceAll("_", " ") ?? "미기록"}</Tag>;
}
