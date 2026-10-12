import { t } from "../i18n/locale";
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
    if (reasons.length === 0) { setMessage(t("reviewDialog.required")); return; }
    setMessage("");
    onSubmit(reasons, note.trim());
  };
  return <Dialog isOpen={target !== null} onClose={close} title={t("reviewDialog.title")}>
    <div className={Classes.DIALOG_BODY}>
      {target && <>
        <p><strong>{target.statement}</strong></p>
        <p className="muted">{t("labels.target")}{" "}{target.evidenceLabel}</p>
      </>}
      <p>{t("reviewDialog.notice")}</p>
      <fieldset className="review-reasons"><legend>{t("traceHistory.reason")}</legend>
        {reviewReasons.map(reason => <Checkbox key={reason.code} label={reason.label} checked={reasons.includes(reason.code)} onChange={() => toggle(reason.code)}/>)}
      </fieldset>
      <label className="review-note">{t("recheck.optionalNote")}<TextArea fill rows={3} aria-label={t("recheck.note")} value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></label>
      {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
      {error && <Callout compact intent="danger" role="alert">{t("reviewDialog.failed")}{" "}{error}</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>{t("hold.close")}</Button>
      <Button intent="primary" loading={pending} onClick={submit}>{t("reviewDialog.send")}</Button>
    </div></div>
  </Dialog>;
}
