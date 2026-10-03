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
      setNotice(`수정 제안 결과: ${memoryStateLabels.transition(result.value.transition)} — ${memoryEditOutcomeLine(result.value.transition, result.value.revision?.support_status)}`);
      await client.invalidateQueries({ queryKey: key });
    },
    onError: async error => {
      const failure = describeEditFailure(error);
      if (failure.stale) { setTarget(null); setNotice(failure.message); await client.invalidateQueries({ queryKey: key }); }
    },
  });
  if (query.isPending) return <p className="muted" role="status">기억을 읽는 중…</p>;
  if (query.error || !query.data) {
    return <Callout intent="warning" role="alert">프로젝트 기억을 읽지 못했습니다. 기억이 없다는 뜻이 아닙니다.
      <Button small onClick={() => void query.refetch()}>다시 읽기</Button></Callout>;
  }
  const { revisions, counts } = query.data.value;
  const ordered = [...revisions].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return <section className="project-memory" data-memory-ready>
    <MemoryInjectionSwitch projectId={projectId}/>
    <p>{counts.total === 0 ? "이 목록에 저장된 기억 기록이 없습니다."
      : `저장된 기억 기록 ${counts.total}개 · 질문에 맞으면 불러올 수 있는 기록 ${counts.recallable}개 · 불러오지 않는 기록 ${counts.not_recalled}개`}</p>
    {notice && <Callout compact role="status" data-memory-edit-result>{notice}</Callout>}
    {ordered.map(memory => <article className={`detail-card memory-card ${memory.is_latest ? "" : "older"}`} key={memory.memory_revision_id}>
      <div className="card-kicker">
        <span className="bp6-tag bp6-minimal">{statusLabel(memory.kind) ?? memory.kind}</span>
        <span className="bp6-tag bp6-minimal">{memoryStateLabels.transition(memory.transition)}</span>
        <span className="bp6-tag bp6-minimal">{memory.is_latest ? "현재 버전" : "새 버전으로 대체됨"}</span>
        {isUserCorrection(memory) && <span className="bp6-tag bp6-minimal">사용자 정정</span>}
      </div>
      <p>{memory.summary}</p>
      <p className="memory-facts"><small>{memoryStateLabels.support(memory.support_status)} · {memoryStateLabels.authority(memory.authority_status)} · {memory.cutoff_valid ? "기준시점 적합" : "기준시점 부적합"} · 근거 {memory.evidence_count}개</small></p>
      {memory.not_recalled_because && <p className="memory-reason">불러오지 않는 이유: {memoryExclusionLabel(memory.not_recalled_because)}</p>}
      {memory.is_latest && hasStaleBasis(memory) && <small className="muted">{staleBasisNotice}</small>}
      {memory.is_latest && !hasStaleBasis(memory) && <Button small onClick={() => { send.reset(); setTarget(memory); }}>
        {isUserCorrection(memory) && memory.transition !== "COMMIT" ? "다시 제안" : "수정 제안"}</Button>}
      <details className="connection-tech"><summary>기술 정보</summary>
        <small>기억 {memory.memory_id}</small><small>기억 버전 {memory.memory_revision_id}</small>
        <small>원본 버전 {memory.owner_revision_ref}</small><small>기록 지문 {memory.revision_digest}</small></details>
    </article>)}
    <small className="muted">실제 조사에서 어떤 기억을 불러올지는 질문마다 달라서 이 화면에서 정하지 않습니다. 기억은 직접 고치지 않고 수정 제안으로 새 버전을 만들며, 원래 기억은 이력에 남습니다.</small>
    <MemoryEditDialog key={target?.revision_digest ?? "closed"} target={target} original={target && actualTarget(target, revisions)} pending={send.isPending} error={target && send.error ? describeEditFailure(send.error).message : null} onClose={() => setTarget(null)}
      onSubmit={input => target && send.mutate({ memory: target, ...input })}/>
  </section>;
}
