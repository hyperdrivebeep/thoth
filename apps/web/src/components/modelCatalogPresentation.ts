import { message as formatMessage } from "../i18n/formatMessages";
import { messages } from "../i18n/messages";
import { t } from "../i18n/locale";
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
  if (Number.isNaN(at.getTime())) return t("history.unknownTime");
  const clock = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
    - new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime()) / 86_400_000);
  if (days === 0) return formatMessage("today", clock);
  if (days === 1) return formatMessage("yesterday", clock);
  return formatMessage("monthDay", at.getMonth()+1, at.getDate(), clock);
}

export const staleNotice = messages["modelHelp.staleCatalog"].ko;
export const staleCatalogNotice = () => t("modelHelp.staleCatalog");

/** One line under the picker: the check time and state of the list the user is choosing from. */
export function catalogLine(row: CatalogStatusRow | undefined, now: Date = new Date()): string | null {
  if (!row) return null;
  if (row.status === "UNAVAILABLE") return t("catalog.notChecked");
  if (row.source !== "PROVIDER_LIST" || !row.fetched_at) return t("catalog.static");
  return formatMessage("catalogChecked", checkedAt(row.fetched_at, now), row.status === "ACTIVE" ? t("catalog.current") : t("catalog.stale"));
}

/** What to say next to one option; the model itself is never hidden by these. */
export function optionNote(option: CatalogOption): string | null {
  if (option.execution === "REJECTED") return t("catalog.executionRejected");
  if (option.execution === "VERIFIED") return null;
  return option.entitlement === "PROVIDER_LISTED" ? null : t("catalog.accountUnverified");
}

export function optionText(option: CatalogOption, provider: string): string {
  const note = optionNote(option);
  return `${option.label ?? option.model} · ${provider}${note ? ` · ${note}` : ""}`;
}

export const excludedReasonLabels: Record<string, string> = {
  get UNSUPPORTED_SLUG() { return t("catalog.unsupported"); }, get UNKNOWN_EFFORT_ONLY() { return t("catalog.unknownEffort"); }, get NAMESPACED_ID() { return t("catalog.otherRoute"); },
};

export function excludedModels(rows: CatalogStatusRow[] | undefined) {
  return (rows ?? []).flatMap(row => (row.excluded ?? []).map(item => ({ ...item, provider: row.provider })));
}

/** The lines shown after "모델 목록 불러오기": each list's check time and, if the refresh failed, what was kept. */
export function catalogResultLines(rows: CatalogStatusRow[] | undefined, label: (provider: string) => string, now: Date = new Date()): string[] {
  const lines = (rows ?? []).flatMap(row => { const line = catalogLine(row, now); return line ? [`${label(row.provider)} · ${line}`] : []; });
  return (rows ?? []).some(row => row.status === "STALE_LAST_GOOD") ? [...lines, staleCatalogNotice()] : lines;
}
