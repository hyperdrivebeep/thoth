import { uiText } from "../i18n/locale";
import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { rpc } from "../api/rpcClient";
import { MemoryEditDialog } from "./MemoryEditDialog";
import { MemoryInjectionSwitch } from "./MemoryInjectionSwitch";
import { actualTarget, describeEditFailure, hasStaleBasis, isUserCorrection, memoryEditOutcomeLine, staleBasisNotice, type MemoryRevision } from "./memoryEdit";
import { memoryExclusionLabel, memoryStateLabels, statusLabel } from "./statusLabels";

type RevisionList = { revisions: MemoryRevision[]; counts: { total: number; recallable: number; not_recalled: number } };

/** Every stored memory version with its review result and why it would or would not be recalled (memory/revision/list), and a correction proposal per current version. */
export function ProjectMemoryPanel({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["memory-revisions", projectId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => rpc<RevisionList>("memory/revision/list", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const [target, setTarget] = useState<MemoryRevision | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const send = useMutation({
    mutationFn: (input: { memory: MemoryRevision; text: string; reason: string; evidenceRefs: string[] }) => rpc<{ transition: string; revision: MemoryRevision }>("memory/edit/propose", {
      project_id: projectId, target_revision_digest: input.memory.revision_digest, corrected_text: input.text, reason: input.reason, evidence_refs: input.evidenceRefs,
    }, crypto.randomUUID()),
    onSuccess: async result => {
      setTarget(null);
      setNotice(formatMessage("memoryOutcome", memoryStateLabels.transition(result.value.transition), memoryEditOutcomeLine(result.value.transition, result.value.revision?.support_status)));
      await client.invalidateQueries({ queryKey: key });
    },
    onError: async error => {
      const failure = describeEditFailure(error);
      if (failure.stale) { setTarget(null); setNotice(failure.message); await client.invalidateQueries({ queryKey: key }); }
    },
  });
  if (query.isPending) return <p className="muted" role="status">{t("memory.loading")}</p>;
  if (query.error || !query.data) {
    return <Callout intent="warning" role="alert">{t("memory.failed")}<Button small onClick={() => void query.refetch()}>{t("workspace.reload")}</Button></Callout>;
  }
  const { revisions, counts } = query.data.value;
  const ordered = [...revisions].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return <section className="project-memory" data-memory-ready>
    <MemoryInjectionSwitch projectId={projectId}/>
    <p>{counts.total === 0 ? t("memory.empty")
      : formatMessage("memoryCounts", counts.total, counts.recallable, counts.not_recalled)}</p>
    {notice && <Callout compact role="status" data-memory-edit-result>{notice}</Callout>}
    {ordered.map(memory => <article className={`detail-card memory-card ${memory.is_latest ? "" : "older"}`} key={memory.memory_revision_id}>
      <div className="card-kicker">
        <span className="bp6-tag bp6-minimal">{statusLabel(memory.kind) ?? memory.kind}</span>
        <span className="bp6-tag bp6-minimal">{memoryStateLabels.transition(memory.transition)}</span>
        <span className="bp6-tag bp6-minimal">{memory.is_latest ? t("memory.current") : t("memory.superseded")}</span>
        {isUserCorrection(memory) && <span className="bp6-tag bp6-minimal">{t("memory.correction")}</span>}
      </div>
      <p>{memory.summary}</p>
      <p className="memory-facts"><small>{formatMessage("memoryFacts", memoryStateLabels.support(memory.support_status), memoryStateLabels.authority(memory.authority_status), memory.cutoff_valid ? t("memory.cutoffEligible") : t("memory.cutoffIneligible"), memory.evidence_count)}</small></p>
      {memory.not_recalled_because && <p className="memory-reason">{t("labels.recallExclusion")}{" "}{memoryExclusionLabel(memory.not_recalled_because)}</p>}
      {memory.is_latest && hasStaleBasis(memory) && <small className="muted">{uiText(staleBasisNotice)}</small>}
      {memory.is_latest && !hasStaleBasis(memory) && <Button small onClick={() => { send.reset(); setTarget(memory); }}>
        {isUserCorrection(memory) && memory.transition !== "COMMIT" ? t("memory.proposeAgain") : t("memory.proposeEdit")}</Button>}
      <details className="connection-tech"><summary>{t("oauth.technical")}</summary>
        <small>{t("memory.memory")}{" "}{memory.memory_id}</small><small>{t("memory.version")}{" "}{memory.memory_revision_id}</small>
        <small>{t("memory.original")}{" "}{memory.owner_revision_ref}</small><small>{t("memory.fingerprint")}{" "}{memory.revision_digest}</small></details>
    </article>)}
    <small className="muted">{t("memory.notice")}</small>
    <MemoryEditDialog key={target?.revision_digest ?? "closed"} target={target} original={target && actualTarget(target, revisions)} pending={send.isPending} error={target && send.error ? describeEditFailure(send.error).message : null} onClose={() => setTarget(null)}
      onSubmit={input => target && send.mutate({ memory: target, ...input })}/>
  </section>;
}
