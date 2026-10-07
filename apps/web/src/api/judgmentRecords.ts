import { rpc } from "./rpcClient";

export type MatchKind = "THIS_HYPOTHESIS" | "ALTERNATIVE" | "NEITHER" | "UNDETERMINED";
/** What a person recorded for one test: the latest result is the one that counts. */
export type TestResult = { event_id: string; hypothesis_id: string; test_id: string; observation: string; matched: MatchKind; evidence_refs: string[]; actor_id: string; created_at: string };
export type DiscriminationItem = {
  hypothesis_id: string; results: TestResult[]; result_history_count: number; refutation_conditions: string[]; conditions_history_count: number;
  elimination: "SINGLE" | "REPEATED" | null;
  /** Where the recorded results leave the hypothesis, read by the server when asked; a record without it counts as none. */
  standing?: "NONE" | "FITS" | "AGAINST_ONCE" | "AGAINST_REPEATED" | "MIXED";
};

export type ClosureKind = "FIX_APPLIED" | "HUMAN_CLOSED" | "WAIVER_RECORDED" | "CONDITION_CHANGED";
export type ClosureEvent = {
  event_id: string; subject_kind: "CRITERION" | "REQUIREMENT"; subject_id: string; kind: ClosureKind; basis_ref: string; note: string; scope: string | null;
  verdict_revision: string; verdict_digest: string; verdict_state: string; actor_id: string; created_at: string;
};
/** A trace row that has a closure: the events, and whether the rules confirm the effect of a recorded fix. */
export type ClosureRow = {
  subject_kind: "CRITERION" | "REQUIREMENT"; subject_id: string; current_state: string | null; effect_confirmed: boolean; effect_verdict_revision: string | null;
  verdict_changed_since: boolean; events: ClosureEvent[];
};

export const discriminationKey = (projectId: string) => ["discrimination", projectId] as const;
export const closuresKey = (projectId: string) => ["trace-closures", projectId] as const;

export async function listDiscrimination(projectId: string, signal?: AbortSignal) {
  return (await rpc<{ items: DiscriminationItem[] }>("hypothesis/test/result/list", { project_id: projectId }, crypto.randomUUID(), signal)).value;
}
export async function recordTestResult(projectId: string, hypothesisId: string, testId: string, observation: string, matched: MatchKind, evidenceRefs: string[]) {
  return (await rpc("hypothesis/test/result/record", { project_id: projectId, hypothesis_id: hypothesisId, test_id: testId, observation, matched, evidence_refs: evidenceRefs }, crypto.randomUUID())).value;
}
export async function recordRefutationConditions(projectId: string, hypothesisId: string, conditions: string[]) {
  return (await rpc("hypothesis/refutation/record", { project_id: projectId, hypothesis_id: hypothesisId, conditions }, crypto.randomUUID())).value;
}
export async function listClosures(projectId: string, signal?: AbortSignal) {
  return (await rpc<{ closures: ClosureRow[] }>("trace/closure/list", { project_id: projectId }, crypto.randomUUID(), signal)).value;
}
export async function recordClosure(input: { projectId: string; subjectKind: string; subjectId: string; kind: ClosureKind; basisRef: string; note: string; scope: string; verdictRevision: string }) {
  return (await rpc("trace/closure/record", { project_id: input.projectId, subject_kind: input.subjectKind, subject_id: input.subjectId, kind: input.kind, basis_ref: input.basisRef,
    note: input.note, ...(input.scope ? { scope: input.scope } : {}), current_verdict_revision: input.verdictRevision }, crypto.randomUUID())).value;
}
