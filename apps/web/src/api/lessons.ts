import { rpc } from "./rpcClient";
import type { MatchKind } from "./judgmentRecords";

export type LessonKind = "TEST_RESULT" | "ELIMINATION" | "EFFECT_CONFIRMED" | "HUMAN_CLOSURE";
export type LessonState = "SAME_CONDITION" | "STALE" | "REFUTED";
export type LessonDetail = {
  observations?: { test_id: string; matched: MatchKind; observation: string; evidence_refs: string[] }[];
  closure_kind?: string; basis_ref?: string; note?: string; scope?: string | null; original_state?: string;
};
/** A lesson: a reference to a record a rule computed or a person made, read back for the row's exact condition. */
export type LessonItem = {
  lesson_id: string; kind: LessonKind; outcome: string; state: LessonState; subject_kind: "CRITERION" | "REQUIREMENT"; subject_id: string;
  hypothesis_id: string | null; test_id: string | null; actor_id: string; created_at: string; detail: LessonDetail;
  /** The other hypotheses a person marked as the same as this lesson's hypothesis; absent when none. */
  same_hypothesis_ids?: string[];
};

export const lessonsKey = (projectId: string) => ["lessons", projectId] as const;

export async function listLessons(projectId: string, signal?: AbortSignal) {
  return (await rpc<{ lessons: LessonItem[] }>("trace/lesson/list", { project_id: projectId }, crypto.randomUUID(), signal)).value;
}
