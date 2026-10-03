// How a provider's model list is described to the user: when it was checked and how much to trust it.
export type CatalogStatusRow = {
  provider: string; source: string; status: "ACTIVE" | "STALE_LAST_GOOD" | "UNAVAILABLE";
  fetched_at: string | null; failure_reason?: string | null; excluded?: { model: string; reason: string }[];
};
export type CatalogOption = { provider: string; model: string; entitlement?: string; execution?: string; label?: string | null };

const pad = (value: number) => String(value).padStart(2, "0");

/** "오늘 14:02", "어제 14:02" or "10월 1일 14:02", in the viewer's time zone. */
export function checkedAt(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "시각 미확인";
  const clock = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
    - new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime()) / 86_400_000);
  if (days === 0) return `오늘 ${clock}`;
  if (days === 1) return `어제 ${clock}`;
  return `${at.getMonth() + 1}월 ${at.getDate()}일 ${clock}`;
}

export const staleNotice = "모델 목록을 새로 받지 못해 마지막으로 확인한 목록을 보여 줍니다. 저장된 선택은 그대로입니다.";

/** One line under the picker: the check time and state of the list the user is choosing from. */
export function catalogLine(row: CatalogStatusRow | undefined, now: Date = new Date()): string | null {
  if (!row) return null;
  if (row.status === "UNAVAILABLE") return "모델 목록을 아직 확인하지 못했습니다";
  if (row.source !== "PROVIDER_LIST" || !row.fetched_at) return "계정 확인 전 · 고정 목록";
  return `마지막 확인: ${checkedAt(row.fetched_at, now)} · ${row.status === "ACTIVE" ? "최신" : "마지막 확인 목록 · 갱신 실패"}`;
}

/** What to say next to one option; the model itself is never hidden by these. */
export function optionNote(option: CatalogOption): string | null {
  if (option.execution === "REJECTED") return "실행 거부됨";
  if (option.execution === "VERIFIED") return null;
  return option.entitlement === "PROVIDER_LISTED" ? null : "계정 확인 전";
}

export function optionText(option: CatalogOption, provider: string): string {
  const note = optionNote(option);
  return `${option.label ?? option.model} · ${provider}${note ? ` · ${note}` : ""}`;
}

export const excludedReasonLabels: Record<string, string> = {
  UNSUPPORTED_SLUG: "ChatGPT 계정에서 쓸 수 없는 모델", UNKNOWN_EFFORT_ONLY: "THOTH가 모르는 추론강도만 있는 모델", NAMESPACED_ID: "다른 경로용 이름의 모델",
};

export function excludedModels(rows: CatalogStatusRow[] | undefined) {
  return (rows ?? []).flatMap(row => (row.excluded ?? []).map(item => ({ ...item, provider: row.provider })));
}

/** The lines shown after "모델 목록 불러오기": each list's check time and, if the refresh failed, what was kept. */
export function catalogResultLines(rows: CatalogStatusRow[] | undefined, label: (provider: string) => string, now: Date = new Date()): string[] {
  const lines = (rows ?? []).flatMap(row => { const line = catalogLine(row, now); return line ? [`${label(row.provider)} · ${line}`] : []; });
  return (rows ?? []).some(row => row.status === "STALE_LAST_GOOD") ? [...lines, staleNotice] : lines;
}
