import { objectList, objectValue, stringValues, textValue } from "../api/presentation";

export type WordPart = { op: "keep" | "add" | "del"; text: string };
export type ApprovalChange = {
  field: string; label: string; before: string; after: string; words: WordPart[];
  attachments: { beforeName: string | null; beforeDigest: string | null; afterName: string | null; afterDigest: string | null }[];
};
export type Approval = {
  id: string; stepId: string; state: string; changes: ApprovalChange[]; payload: Record<string, unknown>;
  reviewNeeded: boolean; payloadCurrent: boolean; revisionDigest: string; createdAt: string;
  predecessors: string[]; targetBaselines: string[]; policyVersion: string;
};

const nullable = (value: unknown) => typeof value === "string" ? value : null;

/** One authorization as read from action/authorization/read (or a record from action/plan/read). */
export function parseApproval(view: Record<string, unknown>): Approval {
  const record = objectValue(view.authorization);
  return {
    id: textValue(record.authorization_id), stepId: textValue(record.step_id),
    state: textValue(view.effective_state) || textValue(record.state), payload: objectValue(record.payload),
    reviewNeeded: view.review_needed === true, payloadCurrent: view.payload_current !== false,
    revisionDigest: textValue(record.revision_digest), createdAt: textValue(record.created_at),
    predecessors: stringValues(record.predecessor_output_digests), targetBaselines: stringValues(record.target_baseline_digests),
    policyVersion: textValue(record.policy_version),
    changes: objectList(record.material_changes).map(item => ({
      field: textValue(item.field), label: textValue(item.label), before: textValue(item.before), after: textValue(item.after),
      words: objectList(item.words).map(word => ({ op: (["add", "del"].includes(textValue(word.op)) ? textValue(word.op) : "keep") as WordPart["op"], text: textValue(word.text) })),
      attachments: objectList(item.attachments).map(pair => ({ beforeName: nullable(pair.before_name), beforeDigest: nullable(pair.before_digest),
        afterName: nullable(pair.after_name), afterDigest: nullable(pair.after_digest) })),
    })),
  };
}

export type Compact = WordPart | { op: "gap"; text: "…" };

/** Only the changed words and a little context around them; the rest is folded into an ellipsis. */
export function compactWords(words: WordPart[], context = 2): Compact[] {
  const changed = words.map((word, index) => word.op !== "keep" ? index : -1).filter(index => index >= 0);
  const shown = new Set<number>();
  for (const index of changed) for (let i = Math.max(0, index - context); i <= Math.min(words.length - 1, index + context); i++) shown.add(i);
  const result: Compact[] = [];
  let previous = -1;
  words.forEach((word, index) => {
    if (!shown.has(index)) return;
    if (index !== previous + 1) result.push({ op: "gap", text: "…" });
    result.push(word); previous = index;
  });
  if (previous !== words.length - 1 && previous >= 0) result.push({ op: "gap", text: "…" });
  return result;
}

export const stateLabels: Record<string, string> = {
  APPROVED: "승인됨 · 이 내용으로 유효", PENDING: "승인 대기", REJECTED: "거절됨", EXPIRED: "만료됨", CONSUMED: "사용됨",
  STALE: "승인 무효 · 내용이 바뀜",
};
export const shortDigest = (digest: string | null) => digest ? digest.slice(0, 8) : "";
export const listOf = (value: unknown) => stringValues(value);
