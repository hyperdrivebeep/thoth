/** Progress labels are positions, never source quotations or inferred file names. */
import { message } from "../i18n/formatMessages";
import { objectList, objectValue, textValue } from "./presentation";
import type { ResearchStatus } from "./research";

const unavailable = () => message("progressLocationUnavailable");
const positiveInteger = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const cellPattern = /^\$?[A-Z]{1,4}\$?[1-9][0-9]*(?::\$?[A-Z]{1,4}\$?[1-9][0-9]*)?$/;

function location(value: unknown): string | null {
  const locator = objectValue(value);
  const parts: string[] = [];
  if (positiveInteger(locator.page)) parts.push(`p.${locator.page}`);
  if (positiveInteger(locator.line_start)) {
    const end = locator.line_end ?? locator.line_start;
    if (positiveInteger(end) && end >= locator.line_start) parts.push(message("progressLineRange", locator.line_start, end));
  }
  if (typeof locator.cell === "string" && locator.cell.length <= 128 && cellPattern.test(locator.cell)) parts.push(locator.cell);
  const text = parts.join(" · ");
  return text ? text.length > 64 ? `${text.slice(0, 63)}…` : text : null;
}

function legacyLocators(status?: ResearchStatus): Record<string, unknown>[] {
  const focus = objectValue(objectValue(status?.attempt?.draft_progress).evidence_focus);
  return objectList(focus.locators).filter(item => "page" in item || "span_id" in item || "exact_text" in item);
}

function legacyLocation(value: Record<string, unknown>, index: number): string {
  return location({ page: value.page }) ?? message("progressLocation", index + 1);
}

export function progressEvidenceLabels(status?: ResearchStatus): string[] {
  const legacy = legacyLocators(status);
  if (status?.user_activity_events === undefined) return legacy.map(legacyLocation);
  const sources = new Map<string, string>();
  for (const [index, event] of objectList(status.user_activity_events).entries()) {
    if (event.schema_version !== "thoth.user_activity_event.v1" || event.activity_kind !== "source" || event.action !== "read") continue;
    const target = objectValue(event.target);
    const label = target.kind === "span" && target.access_state === "allowed" ? location(target.locator) ?? unavailable() : unavailable();
    const sourceRef = textValue(objectValue(event.refs).source_ref);
    const eventId = textValue(event.event_id);
    const key = sourceRef ? `source:${sourceRef}` : eventId ? `event:${eventId}` : `unknown:${index}`;
    const previous = sources.get(key);
    // A contradictory or denied copy must not be overwritten by an allowed one.
    sources.set(key, previous !== undefined && previous !== label ? unavailable() : label);
  }
  // Present-but-empty/unsupported typed data cannot revive old raw location metadata.
  return sources.size ? [...sources.values()] : legacy.map(unavailable);
}

export function progressSourceActionLocation(status: ResearchStatus, payload: Record<string, unknown>, index: number): string {
  // Legacy actions name raw span IDs; typed events expose aliases. Do not zip them by order.
  return status.user_activity_events === undefined ? legacyLocation(payload, index) : unavailable();
}
