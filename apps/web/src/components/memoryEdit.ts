import { message as formatMessage } from "../i18n/formatMessages";
import { messages } from "../i18n/messages";
import { t } from "../i18n/locale";
import { RpcError } from "../api/rpcClient";

export type MemoryRevision = {
  memory_id: string; memory_revision_id: string; revision_digest: string; parent_revision_digest: string | null; owner_revision_ref: string; kind: string;
  summary: string; assertion: string | null; source_ref: string | null; evidence_count: number; evidence_refs: string[]; transition: string; support_status: string;
  authority_status: string; cutoff_valid: boolean; is_latest: boolean; not_recalled_because: string | null; created_at: string;
};
export const researchRunningNotice = messages["memoryEdit.afterRun"].ko;
export const staleBasisNotice = messages["memoryEdit.basisChanged"].ko;

/** The record the memory stands on has changed, so a correction would rest on an outdated basis. */
export const hasStaleBasis = (memory: MemoryRevision) =>
  memory.not_recalled_because === "OWNER_REVISION_NOT_CURRENT" || memory.not_recalled_because === "DEPENDENCY_REVIEW_REQUIRED";

/** A correction that was not accepted never replaced anything, so a new proposal goes to what it would have replaced. */
export function actualTarget(memory: MemoryRevision, all: MemoryRevision[]): MemoryRevision {
  const byDigest = new Map(all.map(item => [item.revision_digest, item]));
  let current = memory;
  while (current.transition !== "COMMIT" && isUserCorrection(current) && current.parent_revision_digest && byDigest.has(current.parent_revision_digest)) {
    current = byDigest.get(current.parent_revision_digest)!;
  }
  return current;
}
export type MemoryEditSubmission = { text: string; reason: string; evidenceRefs: string[] };

/** What each review result means for the user, in one line. */
export const memoryEditOutcomeLines: Record<string, string> = {
  get COMMIT() { return t("memoryEdit.applied"); },
  get REVISE() { return t("memoryEdit.tooVague"); },
  get HOLD() { return t("memoryEdit.ambiguous"); },
  get QUARANTINE() { return t("memoryEdit.quarantined"); },
};

/** A held correction says which kind of hold it is: an unclear relation, or a different value for the same record. */
export function memoryEditOutcomeLine(transition: string, supportStatus?: string): string {
  if (transition === "HOLD" && supportStatus === "CONFLICTING") return t("memoryEdit.conflict");
  return memoryEditOutcomeLines[transition] ?? "";
}

/** A correction is stored under a memory-edit record; the list shows it with a badge. */
export const isUserCorrection = (memory: MemoryRevision) => memory.source_ref?.startsWith("MEMORY:") === true;

export type EditFailure = { message: string; stale: boolean };
export function describeEditFailure(error: unknown): EditFailure {
  const text = error instanceof RpcError ? error.message : "";
  if (text.includes("MEMORY_EDIT_TARGET_STALE")) return { message: formatMessage("refreshedNotice", t("memoryEdit.basisChanged")), stale: true };
  if (text.includes("MEMORY_EDIT_TARGET_SUPERSEDED")) return { get message() { return t("memoryEdit.superseded"); }, stale: true };
  if (text.includes("MEMORY_EDIT_TARGET_NOT_FOUND")) return { get message() { return t("memoryEdit.missing"); }, stale: true };
  return { get message() { return t("memoryEdit.failed"); }, stale: false };
}
