import { rpc } from "./rpcClient";

export type RecheckReasonCode = "UNRELATED" | "STILL_MATCHES" | "NEEDS_RESEARCH" | "OTHER";
export type LinkRecheck = { reason_code: RecheckReasonCode; note: string; actor_id: string; created_at: string; flipped: boolean };
/** A hypothesis that came from a trace row, read against that row's verdict today. */
export type HypothesisLink = {
  hypothesis_id: string; hypothesis_revision_digest: string; statement: string; subject_kind: "CRITERION" | "REQUIREMENT"; subject_id: string; subject_title: string;
  state: "CURRENT" | "STALE" | "RECHECKED"; change: "CONTENT_CHANGED" | "EVIDENCE_ONLY" | "FLIPPED" | "SUBJECT_MISSING" | null;
  link_state: string | null; current_state: string | null; current_verdict_revision: string | null; recheck: LinkRecheck | null;
};
export type ReasonDistribution = { total: number; flipped: number; by_reason: Partial<Record<RecheckReasonCode, number>> };
export type HypothesisLinks = { trace_digest: string | null; links: HypothesisLink[]; reason_distribution: ReasonDistribution };

export const hypothesisLinksKey = (projectId: string) => ["hypothesis-links", projectId] as const;

export async function listHypothesisLinks(projectId: string, signal?: AbortSignal): Promise<HypothesisLinks> {
  return (await rpc<HypothesisLinks>("hypothesis/link/list", { project_id: projectId }, crypto.randomUUID(), signal)).value;
}

export async function recheckHypothesisLinks(projectId: string, hypothesisIds: string[], reason: RecheckReasonCode, note: string, currentVerdictRevision: string | null) {
  return (await rpc<HypothesisLinks & { events: unknown[] }>("hypothesis/link/recheck", {
    project_id: projectId, hypothesis_ids: hypothesisIds, reason_code: reason, note, current_verdict_revision: currentVerdictRevision,
  }, crypto.randomUUID())).value;
}
