import type { TraceView, Verdict } from "../api/trace";
import type { WorkThread } from "../types";
import { titleOf } from "./traceRows";
import { reasonSentence, verdictLook } from "./traceText";

/** What the client names: the row and the revision of its verdict it was looking at. The server writes the rest. */
export type TraceOrigin = {
  kind: "TRACE_VERDICT"; project_id: string; subject_kind: "CRITERION" | "REQUIREMENT"; subject_id: string; verdict_revision: string;
};
export type RowInvestigation = { origin: TraceOrigin; question: string };

const SETTLED = new Set(["PASS_COMPUTED", "PASS"]);
const kindOf = (kind: string): "CRITERION" | "REQUIREMENT" => (kind === "REQUIREMENT" ? "REQUIREMENT" : "CRITERION");

/** A held or failed row has an investigation to start; a met row, even a stale one, and an unknown state have none. */
export function canInvestigate(kind: string, verdict: Verdict): boolean {
  return !SETTLED.has(verdict.state) && verdictLook(kind, verdict.state).intent !== "none";
}

/** The row's origin and the question to put in the composer; nothing is sent until the user sends it. */
export function investigationFor(projectId: string, kind: string, verdict: Verdict, view: TraceView): RowInvestigation | null {
  if (!canInvestigate(kind, verdict)) return null;
  const name = titleOf(view);
  const subject = kindOf(kind);
  const reasons = verdict.reasons.computed.map(code => reasonSentence(code, name, verdict.conditions.condition));
  const failed = !verdict.state.startsWith("HOLD");
  const what = subject === "CRITERION" ? "기준" : "요구사항";
  const ask = failed ? "원인 후보와 그것을 가를 시험을 찾아 주세요." : "판정하려면 무엇이 더 필요한지, 가능한 원인 후보와 그것을 가를 시험을 찾아 주세요.";
  const question = [`「${name(verdict.subject_id)}」 ${what}의 판정이 "${verdictLook(kind, verdict.state).text}"입니다.`, ...reasons, ask].join(" ");
  return { origin: { kind: "TRACE_VERDICT", project_id: projectId, subject_kind: subject, subject_id: verdict.subject_id, verdict_revision: verdict.revision_digest }, question };
}

/** The thread that was started from this row, the newest if there are several; null when none was. */
export function threadForRow(threads: WorkThread[], kind: string, subjectId: string): WorkThread | null {
  const found = threads.filter(thread => thread.origin?.subject_kind === kind && thread.origin.subject_id === subjectId);
  return found.reduce<WorkThread | null>((best, item) => (!best || (item.updated_at ?? "") > (best.updated_at ?? "") ? item : best), null);
}

/** The draft with the question in it: an empty draft becomes the question, other text is kept and the question follows it. */
export function fillQuestion(draft: string, question: string): string {
  if (!draft.trim()) return question;
  return draft.includes(question) ? draft : `${draft}\n\n${question}`;
}
