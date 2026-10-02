import { RpcError } from "../api/rpcClient";

export type MemoryRevision = {
  memory_id: string; memory_revision_id: string; revision_digest: string; parent_revision_digest: string | null; owner_revision_ref: string; kind: string;
  summary: string; assertion: string | null; source_ref: string | null; evidence_count: number; evidence_refs: string[]; transition: string; support_status: string;
  authority_status: string; cutoff_valid: boolean; is_latest: boolean; not_recalled_because: string | null; created_at: string;
};
export const researchRunningNotice = "조사가 끝난 뒤 제안할 수 있습니다";
export const staleBasisNotice = "원래 근거가 바뀌었습니다";

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
  COMMIT: "다음 조사부터 원래 기억 대신 이 정정을 불러올 수 있습니다.",
  REVISE: "내용이 짧거나 구체적이지 않습니다.",
  HOLD: "관계가 불분명해 보류했습니다.",
  QUARANTINE: "명령문이나 비밀값으로 보이는 내용이 있어 저장만 하고 쓰지 않습니다.",
};

/** A held correction says which kind of hold it is: an unclear relation, or a different value for the same record. */
export function memoryEditOutcomeLine(transition: string, supportStatus?: string): string {
  if (transition === "HOLD" && supportStatus === "CONFLICTING") return "같은 기록의 값이 이미 반영된 기억과 달라 보류했습니다.";
  return memoryEditOutcomeLines[transition] ?? "";
}

/** A correction is stored under a memory-edit record; the list shows it with a badge. */
export const isUserCorrection = (memory: MemoryRevision) => memory.source_ref?.startsWith("MEMORY:") === true;

export type EditFailure = { message: string; stale: boolean };
export function describeEditFailure(error: unknown): EditFailure {
  const text = error instanceof RpcError ? error.message : "";
  if (text.includes("MEMORY_EDIT_TARGET_STALE")) return { message: staleBasisNotice + ". 목록을 다시 읽었습니다.", stale: true };
  if (text.includes("MEMORY_EDIT_TARGET_SUPERSEDED")) return { message: "이 기억은 이미 더 새로운 정정으로 대체되었습니다. 목록을 다시 읽었습니다.", stale: true };
  if (text.includes("MEMORY_EDIT_TARGET_NOT_FOUND")) return { message: "이 기억을 찾지 못했습니다. 목록을 다시 읽었습니다.", stale: true };
  return { message: "수정 제안을 보내지 못했습니다. 잠시 뒤 다시 시도해 주세요.", stale: false };
}
