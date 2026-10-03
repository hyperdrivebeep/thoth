import { Button, Callout, Checkbox, Classes, Dialog, TextArea } from "@blueprintjs/core";
import { useState } from "react";
import { reviewReasons, type ReviewTarget } from "./judgmentReview";

/** Collects the reasons and an optional memo for one review request. Sending is the caller's job. */
export function ReviewDialog({ target, pending, error, onClose, onSubmit }: {
  target: ReviewTarget | null; pending: boolean; error: string | null;
  onClose: () => void; onSubmit: (reasons: string[], note: string) => void;
}) {
  const [reasons, setReasons] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const close = () => { setReasons([]); setNote(""); setMessage(""); onClose(); };
  const toggle = (code: string) => setReasons(current => current.includes(code) ? current.filter(item => item !== code) : [...current, code]);
  const submit = () => {
    if (reasons.length === 0) { setMessage("이유를 하나 이상 고르세요."); return; }
    setMessage("");
    onSubmit(reasons, note.trim());
  };
  return <Dialog isOpen={target !== null} onClose={close} title="판단 재검토 요청">
    <div className={Classes.DIALOG_BODY}>
      {target && <>
        <p><strong>{target.statement}</strong></p>
        <p className="muted">대상: {target.evidenceLabel}</p>
      </>}
      <p>AI가 근거를 다시 살펴 유지·변경·보류를 판단합니다. 이 요청만으로 판단 값이 바뀌지는 않습니다. 조사가 진행 중이면 끝난 뒤에 반영합니다.</p>
      <fieldset className="review-reasons"><legend>이유</legend>
        {reviewReasons.map(reason => <Checkbox key={reason.code} label={reason.label} checked={reasons.includes(reason.code)} onChange={() => toggle(reason.code)}/>)}
      </fieldset>
      <label className="review-note">메모 (선택)
        <TextArea fill rows={3} aria-label="메모" value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></label>
      {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
      {error && <Callout compact intent="danger" role="alert">재검토 요청을 보내지 못했습니다. {error}</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>닫기</Button>
      <Button intent="primary" loading={pending} onClick={submit}>재검토 요청 보내기</Button>
    </div></div>
  </Dialog>;
}
