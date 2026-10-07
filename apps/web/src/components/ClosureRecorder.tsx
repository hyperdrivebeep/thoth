import { Button, Callout, Classes, Dialog, InputGroup, Radio, RadioGroup, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { closuresKey, recordClosure, type ClosureKind } from "../api/judgmentRecords";
import type { Verdict } from "../api/trace";
import { CLOSURE_KINDS, CLOSURE_NOTE } from "./judgmentRecordText";

/** Collects what a person decided about a row that was not met, with the document it rests on. The verdict is not changed. */
export function ClosureRecorder({ projectId, verdict, title, onClose }: { projectId: string; verdict: Verdict | null; title: string; onClose: () => void }) {
  const client = useQueryClient();
  const [kind, setKind] = useState<ClosureKind | "">("");
  const [basis, setBasis] = useState("");
  const [scope, setScope] = useState("");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const close = () => { setKind(""); setBasis(""); setScope(""); setNote(""); setMessage(""); onClose(); };
  const send = useMutation({
    mutationFn: () => recordClosure({ projectId, subjectKind: verdict!.subject_kind, subjectId: verdict!.subject_id, kind: kind as ClosureKind, basisRef: basis.trim(),
      note: note.trim(), scope: kind === "CONDITION_CHANGED" ? scope.trim() : "", verdictRevision: verdict!.revision_digest }),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: closuresKey(projectId) }); close(); },
    onError: () => void client.invalidateQueries({ queryKey: closuresKey(projectId) }),
  });
  const submit = () => {
    if (kind === "") { setMessage("처분 종류를 고르세요."); return; }
    if (!basis.trim()) { setMessage("근거 문서를 적어 주세요."); return; }
    if (kind === "CONDITION_CHANGED" && !scope.trim()) { setMessage("해당 조건 범위를 적어 주세요."); return; }
    setMessage(""); send.mutate();
  };
  return <Dialog isOpen={verdict !== null} onClose={close} title={"처분 기록 · " + title}>
    <div className={Classes.DIALOG_BODY}>
      <p>{CLOSURE_NOTE}</p>
      <RadioGroup label="처분 종류" selectedValue={kind} onChange={event => setKind(event.currentTarget.value as ClosureKind)}>
        {CLOSURE_KINDS.map(item => <Radio key={item.code} value={item.code} label={item.label}/>)}
      </RadioGroup>
      {kind !== "" && <p className="muted">{CLOSURE_KINDS.find(item => item.code === kind)?.hint}</p>}
      <label className="review-note">근거 문서 (필수)
        <InputGroup fill aria-label="근거 문서" placeholder="문서 번호, 회의록, 변경 요청서 등" value={basis} maxLength={500} onChange={event => setBasis(event.target.value)}/></label>
      {kind === "CONDITION_CHANGED" && <label className="review-note">해당 조건 범위 (필수)
        <InputGroup fill aria-label="해당 조건 범위" value={scope} maxLength={500} onChange={event => setScope(event.target.value)}/></label>}
      <label className="review-note">메모 (선택)
        <TextArea fill rows={2} aria-label="메모" value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></label>
      {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
      {send.error && <Callout compact intent="danger" role="alert">기록하지 못했습니다. 판정이 그 사이 바뀌었을 수 있으니 화면을 새로 읽은 뒤 다시 시도해 주세요.</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>닫기</Button>
      <Button intent="primary" loading={send.isPending} onClick={submit}>기록 남기기</Button>
    </div></div>
  </Dialog>;
}
