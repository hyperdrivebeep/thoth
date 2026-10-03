import type { ResearchStatus } from "../api/research";

const none = "이 결과의 사용량 기록 없음";

function minutes(ms: number): string {
  return ms < 60_000 ? "1분 미만" : `${Math.round(ms / 60_000)}분`;
}

/** Wall-clock time when the server recorded a start and an end; else the model time summed over this attempt's stages. */
function timeText(status: ResearchStatus, operationId: string, wallMs: number | null | undefined): string {
  if (typeof wallMs === "number" && wallMs >= 0) return `걸린 시간 ${minutes(wallMs)}`;
  // Stage times belong to the attempt the status describes, so they only stand in for that operation.
  const stages = status.request?.operation_id === operationId
    ? (status.completed_stages ?? []).map(stage => stage.elapsed_ms).filter((value): value is number => typeof value === "number" && value >= 0) : [];
  return stages.length ? `모델 처리 시간 합 ${minutes(stages.reduce((sum, value) => sum + value, 0))}` : "시간 미확인";
}

/**
 * Usage under one result, from the server's per-operation figures only. Unknown values stay unknown and are never
 * shown as zero; a result with no recorded calls says so.
 */
export function resultUsageLine(status: ResearchStatus | undefined, operationId: string | undefined): string {
  const usage = operationId ? status?.result_usage?.[operationId] : undefined;
  if (!status || !operationId || !usage) return none;
  const tokens = usage.total_tokens == null ? "미확인" : usage.total_tokens.toLocaleString() + (usage.state === "PARTIAL" ? " (부분 관측)" : "");
  // The stage counts and the retry count describe what this run did; they are never shown for another result.
  const reuse = status.request?.operation_id === operationId ? status.stage_reuse : undefined;
  const reused = reuse && reuse.reused > 0 ? ` · 완료된 ${reuse.reused}단계 재사용 · 새로 부른 단계 ${reuse.new}` : "";
  const retried = usage.auto_retries ? ` · 자동 재시도 ${usage.auto_retries}회(연결 끊김)` : "";
  return `이 조사: 토큰 ${tokens} · ${timeText(status, operationId, usage.wall_ms)}${reused}${retried} · 추정 비용 미확인`;
}

