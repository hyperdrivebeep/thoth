import { Button, Callout, Checkbox, Classes, Dialog, TextArea } from "@blueprintjs/core";
import { useState } from "react";
import type { MemoryEditSubmission, MemoryRevision } from "./memoryEdit";

/** Collects the corrected text and the reason for one memory. Sending is the caller's job. */
export function MemoryEditDialog({ target, original, pending, error, onClose, onSubmit }: {
  target: MemoryRevision | null; original: MemoryRevision | null; pending: boolean; error: string | null;
  onClose: () => void; onSubmit: (input: MemoryEditSubmission) => void;
}) {
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [withEvidence, setWithEvidence] = useState(false);
  const [message, setMessage] = useState("");
  const close = () => { setText(""); setReason(""); setWithEvidence(false); setMessage(""); onClose(); };
  const submit = () => {
    if (!text.trim() || !reason.trim()) { setMessage("고친 내용과 이유를 모두 적어 주세요."); return; }
    setMessage("");
    onSubmit({ text: text.trim(), reason: reason.trim(), evidenceRefs: withEvidence && original ? original.evidence_refs : [] });
  };
  return <Dialog isOpen={target !== null} onClose={close} title="기억 수정 제안">
    <div className={Classes.DIALOG_BODY}>
      {original && <p className="muted">원래 기억: {original.summary}</p>}
      <p>제안은 검토를 거쳐 반영되면 새 버전이 됩니다. 원래 기억은 지워지지 않고 이력에 남습니다.</p>
      <label className="review-note">고친 내용
        <TextArea fill rows={3} aria-label="고친 내용" value={text} maxLength={2000} onChange={event => setText(event.target.value)}/></label>
      <label className="review-note">이유
        <TextArea fill rows={2} aria-label="이유" value={reason} maxLength={500} onChange={event => setReason(event.target.value)}/></label>
      {original && original.evidence_refs.length > 0 &&
        <Checkbox label={`원래 기억의 근거 ${original.evidence_refs.length}개를 함께 연결`} checked={withEvidence} onChange={() => setWithEvidence(value => !value)}/>}
      {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
      {error && <Callout compact intent="danger" role="alert">{error}</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>닫기</Button>
      <Button intent="primary" loading={pending} onClick={submit}>수정 제안 보내기</Button>
    </div></div>
  </Dialog>;
}
