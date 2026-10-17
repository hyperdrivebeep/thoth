import { t } from "../i18n/locale";
import { Callout } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { objectList, objectValue, textValue } from "../api/presentation";
import { rpc } from "../api/rpcClient";
import { ApprovalCard } from "./ApprovalCard";
import { parseApproval, type Approval } from "./approvalView";
import { useReadAccessProtection } from "./useReadAccessProtection";

/**
 * The plan's protected-step approvals, newest per step. Shows nothing when they cannot be read.
 * "Make the approval request again" only prepares a pending request for the current content; approvers still decide it.
 * "보내지 않음" is remembered on this screen only: no message is sent either way.
 */
export function ApprovalSection({ projectId, planId, onReapprove, onDecline }: {
  projectId: string; planId: string; onReapprove?: (approval: Approval) => void; onDecline?: (approval: Approval) => void;
}) {
  const client = useQueryClient();
  const key = ["approvals", projectId, planId];
  const [declined, setDeclined] = useState<string[]>([]);
  const query = useQuery({
    queryKey: key, retry: false,
    queryFn: async ({ signal }) => {
      const plan = await rpc<Record<string, unknown>>("action/plan/read", { project_id: projectId, plan_id: planId }, crypto.randomUUID(), signal);
      const latest = new Map<string, Record<string, unknown>>();
      for (const record of objectList(plan.value.authorizations)) {
        const seen = latest.get(textValue(record.step_id));
        if (!seen || textValue(record.created_at) >= textValue(seen.created_at)) latest.set(textValue(record.step_id), record);
      }
      const approvals = await Promise.all([...latest.values()].map(async record => parseApproval(objectValue((await rpc<Record<string, unknown>>("action/authorization/read",
        { project_id: projectId, authorization_id: textValue(record.authorization_id) }, crypto.randomUUID(), signal)).value))));
      return { approvals, planRevision: textValue(objectValue(plan.value.plan).revision_digest) };
    },
  });
  const reapprove = useMutation({
    mutationFn: (approval: Approval) => rpc("action/authorization/prepare", { project_id: projectId, plan_id: planId, step_id: approval.stepId,
      plan_revision_digest: query.data?.planRevision ?? "", predecessor_output_digests: approval.predecessors, target_baseline_digests: approval.targetBaselines,
      policy_version: approval.policyVersion }, crypto.randomUUID()),
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  });
  const blocked = useReadAccessProtection(key, query.error);
  const requested = reapprove.isSuccess;
  const shown = (query.data?.approvals ?? []).filter(approval => !declined.includes(approval.id));
  if (blocked || !query.data || query.data.approvals.length === 0) return null;
  return <>
    {reapprove.error && <Callout compact intent="danger" role="alert">{t("approval.recreateFailed")}{" "}{reapprove.error.message}</Callout>}
    {requested && <Callout compact intent="primary" role="status">{t("approval.created")}</Callout>}
    {shown.map(approval => <ApprovalCard key={approval.id} approval={approval}
      onReapprove={() => { onReapprove?.(approval); if (!onReapprove) reapprove.mutate(approval); }}
      onDecline={() => { onDecline?.(approval); setDeclined(current => [...current, approval.id]); }}/>)}
    {declined.length > 0 && <p className="muted">{t("approval.notSentNotice")}</p>}
  </>;
}
