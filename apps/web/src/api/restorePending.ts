import { z } from "zod";
import type { HistoryScope, RestoreSelection } from "./historyModels";
import { restoreAttemptSchema, type RestoreAttempt } from "./restore";

const pendingSchema = z.object({ version: z.literal(1), attempt: restoreAttemptSchema, operationId: z.string().nullable() });
export type PendingRestore = z.infer<typeof pendingSchema>;
// Head changes must not hide an in-flight attempt. Actor/session/scope changes must.
function storageKey(scope: HistoryScope, selection: RestoreSelection, actorScope: string): string {
  return `thoth:restore-pending:v1:${JSON.stringify([scope.projectId, scope.threadId, scope.requestDigest,
    selection.entityType, selection.entityId, selection.targetDigest, actorScope])}`;
}
export function loadPendingRestore(scope: HistoryScope, selection: RestoreSelection, actorScope: string): PendingRestore | null {
  try {
    const value = pendingSchema.parse(JSON.parse(sessionStorage.getItem(storageKey(scope, selection, actorScope)) ?? "null"));
    const input = value.attempt.input;
    if (value.attempt.principalScope !== actorScope || input.project_id !== scope.projectId || input.selection.project_id !== scope.projectId
      || input.selection.entity_id !== selection.entityId || input.selection.entity_type !== selection.entityType
      || input.selection.target_revision_digest !== selection.targetDigest) return null;
    return value;
  } catch { return null; }
}
export function savePendingRestore(scope: HistoryScope, selection: RestoreSelection, attempt: RestoreAttempt, operationId: string | null) {
  try { sessionStorage.setItem(storageKey(scope, selection, attempt.principalScope), JSON.stringify({ version: 1, attempt, operationId })); }
  catch { /* In-memory attempt remains valid; no new key is issued on a transport failure. */ }
}
export function clearPendingRestore(scope: HistoryScope, selection: RestoreSelection, actorScope: string) {
  try { sessionStorage.removeItem(storageKey(scope, selection, actorScope)); } catch { /* Optional convenience storage. */ }
}
