import { z } from "zod";
import { rpc } from "./rpcClient";
import { changeGroupSchema, coverageSchema, diffEntrySchema, digestSchema, type WireChange, type WireChangeGroup } from "./historySchemas";
import type { RevisionComparison, RestoreSelection } from "./historyModels";

const comparisonSchema = z.object({ contract_version: z.literal(2), from_revision_digest: digestSchema,
  to_revision_digest: digestSchema, diff: z.array(diffEntrySchema), summary_groups: z.array(changeGroupSchema), coverage: coverageSchema });

export function mapComparison(changes: WireChange[], groups: WireChangeGroup[], coverage = "COMPLETE", reasons: string[] = []): RevisionComparison {
  return { coverage, reasons, changes: changes.map(change => {
    const group = groups.find(item => item.trace_paths.includes(change.path));
    return { path: change.path, before: change.before, after: change.after,
      beforeMissing: !change.before_present, afterMissing: !change.after_present,
      group: group?.kind ?? "OTHER", label: group?.summary ?? "내용 변경" };
  }) };
}

export async function compareHistory(selection: RestoreSelection, signal?: AbortSignal): Promise<RevisionComparison> {
  const response = await rpc<unknown>("revision/diff/read", { project_id: selection.projectId,
    from_revision_digest: selection.targetDigest, to_revision_digest: selection.expectedHead, contract_version: 2 }, crypto.randomUUID(), signal);
  if (response.state !== "SUCCEEDED") throw new Error("비교가 완료되지 않았습니다.");
  const value = comparisonSchema.parse(response.value);
  if (value.from_revision_digest !== selection.targetDigest || value.to_revision_digest !== selection.expectedHead) {
    throw new Error("비교 대상의 버전 연결을 확인하지 못했습니다.");
  }
  return mapComparison(value.diff, value.summary_groups, value.coverage.association === "EXACT" ? "COMPLETE" : "PARTIAL", value.coverage.reasons);
}
