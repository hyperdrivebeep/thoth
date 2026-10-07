import { rpc } from "./rpcClient";

/** What a screen shows about a hypothesis a person marked as the same as another; empty for one that cannot be read. */
export type SameMember = { statement?: string; investigated_at?: string; subject_kind?: string; subject_id?: string };
/** The groups of hypotheses people marked as the same one across investigations (each with at least two ids). */
export type SameView = { groups: string[][]; pairs: string[][]; members: Record<string, SameMember>; events: unknown[] };

export const sameKey = (projectId: string) => ["hypothesis-same", projectId] as const;

/** An answer that lacks a part reads as no group, so a screen never fails on a short or unexpected answer. */
const whole = (value: Partial<SameView> | undefined): SameView => ({ groups: value?.groups ?? [], pairs: value?.pairs ?? [], members: value?.members ?? {}, events: value?.events ?? [] });

export async function listSame(projectId: string, signal?: AbortSignal) {
  return whole((await rpc<SameView>("hypothesis/same/list", { project_id: projectId }, crypto.randomUUID(), signal)).value);
}

export async function recordSame(projectId: string, hypothesisIds: [string, string], action: "LINK" | "UNLINK", note: string) {
  return (await rpc<SameView>("hypothesis/same/record", { project_id: projectId, hypothesis_ids: hypothesisIds, action, note }, crypto.randomUUID())).value;
}
