import { Button, Callout, Classes, Dialog, TextArea } from "@blueprintjs/core";
import { useState } from "react";
import { verdictLook } from "./traceText";
import type { Verdict } from "../api/trace";

/** Collects the reason for a person's confirmation. Sending is the caller's job; a confirmation never changes the verdict. */
export function TraceConfirmDialog({ verdict, title, pending, error, onClose, onSubmit }: {
  verdict: Verdict | null; title: string; pending: boolean; error: string | null; onClose: () => void; onSubmit: (rationale: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const close = () => { setReason(""); setMessage(""); onClose(); };
  const submit = () => { if (!reason.trim()) { setMessage("확인한 이유를 적어 주세요."); return; } setMessage(""); onSubmit(reason.trim()); };
  return <Dialog isOpen={verdict !== null} onClose={close} title="판정 확인 기록">
    <div className={Classes.DIALOG_BODY}>
      {verdict && <p><strong>{title}</strong>: {verdictLook(verdict.subject_kind, verdict.state).text}</p>}
      <p>확인은 사람이 이 판정을 보았다는 기록만 남깁니다. 판정은 바뀌지 않고, 나중에 다시 계산되면 새 판정에는 이 확인이 옮겨지지 않습니다.</p>
      <label className="review-note">확인한 이유
        <TextArea fill rows={3} aria-label="확인한 이유" value={reason} maxLength={2000} onChange={event => setReason(event.target.value)}/></label>
      {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
      {error && <Callout compact intent="danger" role="alert">{error}</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>닫기</Button>
      <Button intent="primary" loading={pending} onClick={submit}>확인 기록 남기기</Button>
    </div></div>
  </Dialog>;
}
