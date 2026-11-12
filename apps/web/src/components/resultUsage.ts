import { t, localeTag } from "../i18n/locale";
import { message as formatMessage } from "../i18n/formatMessages";
import type { ResearchStatus } from "../api/research";

const noUsage = () => t("usage.none");

function minutes(ms: number): string {
  return ms < 60_000 ? t("usage.lessMinute") : formatMessage("minutes", Math.round(ms / 60_000));
}

/** Wall-clock time when the server recorded a start and an end; else the model time summed over this attempt's stages. */
function timeText(
  status: ResearchStatus,
  operationId: string,
  wallMs: number | null | undefined,
): string {
  if (typeof wallMs === "number" && wallMs >= 0)
    return formatMessage("elapsedTime", minutes(wallMs));
  // Stage times belong to the attempt the status describes, so they only stand in for that operation.
  const stages =
    status.request?.operation_id === operationId
      ? (status.completed_stages ?? [])
          .map((stage) => stage.elapsed_ms)
          .filter((value): value is number => typeof value === "number" && value >= 0)
      : [];
  return stages.length
    ? formatMessage("modelTime", minutes(stages.reduce((sum, value) => sum + value, 0)))
    : t("usage.timeUnknown");
}

/**
 * Usage under one result, from the server's per-operation figures only. Unknown values stay unknown and are never
 * shown as zero; a result with no recorded calls says so.
 */
export function resultUsageLine(
  status: ResearchStatus | undefined,
  operationId: string | undefined,
): string {
  const usage = operationId ? status?.result_usage?.[operationId] : undefined;
  if (!status || !operationId || !usage) return noUsage();
  const tokens =
    usage.total_tokens == null
      ? t("state.unknown")
      : usage.total_tokens.toLocaleString(localeTag()) +
        (usage.state === "PARTIAL" ? t("workspace.partialUsage") : "");
  // The stage counts and the retry count describe what this run did; they are never shown for another result.
  const reuse = status.request?.operation_id === operationId ? status.stage_reuse : undefined;
  const reused =
    reuse && reuse.reused > 0 ? formatMessage("reusedStages", reuse.reused, reuse.new) : "";
  const retried = usage.auto_retries ? formatMessage("autoRetries", usage.auto_retries) : "";
  // The totals already include these calls; the share is shown so it is not a hidden cost.
  // When the total is unknown, a share that is also unknown would only say "unknown" a second time.
  const apart = Object.entries(usage.by_purpose ?? {})
    .filter(([, part]) => usage.total_tokens != null || part.total_tokens != null)
    .map(([purpose, part]) =>
      formatMessage(
        "usagePart",
        purpose === "MEMORY_QUERY_EXPANSION" ? t("usage.memoryExpansion") : part.label,
        part.total_tokens == null ? null : part.total_tokens.toLocaleString(localeTag()),
      ),
    )
    .join("");
  return formatMessage(
    "resultUsage",
    tokens,
    timeText(status, operationId, usage.wall_ms),
    reused,
    retried,
    apart,
  );
}

/**
 * True when a finished run's usage is written under its result card ("이 조사: …"). The line under the
 * question box then would say the same a second time. While research runs, or when it ended without a
 * result on screen, there is no such card line and the line under the question box is the only one.
 */
export function usageShownUnderResult(
  status: Pick<ResearchStatus, "operation_state" | "current_result"> | undefined,
): boolean {
  return (
    status !== undefined &&
    status.operation_state !== "RUNNING" &&
    Boolean(status.current_result?.result)
  );
}
