import type { ResearchStatus } from "./research";
import type { HistoryScope } from "./historyModels";

/** Pin a displayed checkpoint only when every identity comes from the same read. */
export function currentResultDigest(status: ResearchStatus | undefined, scope: HistoryScope, operationId: string): string | undefined {
  const manifest = status?.current_result ?? status?.previous_result;
  const attempt = status?.attempt;
  const checkpoint = attempt?.checkpoint_ref;
  if (!status || !manifest || !attempt || !checkpoint ||
      status.project_id !== scope.projectId || status.thread_id !== scope.threadId ||
      manifest.operation_id !== operationId || attempt.operation_id !== operationId ||
      manifest.request_ref?.revision_digest !== scope.requestDigest ||
      attempt.request_ref?.revision_digest !== scope.requestDigest ||
      checkpoint.project_id !== scope.projectId || checkpoint.entity_type !== "DECISION_OBJECT" ||
      checkpoint.entity_id !== `result:${scope.threadId}` || !/^[a-f0-9]{64}$/i.test(checkpoint.revision_digest)) return undefined;
  return checkpoint.revision_digest;
}
