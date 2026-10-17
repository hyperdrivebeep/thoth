import type { MessageKey } from "../i18n/messages";
import { message as formatMessage } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, Checkbox, Classes, Dialog, TextArea } from "@blueprintjs/core";
import { useState } from "react";
import type { MemoryEditSubmission, MemoryRevision } from "./memoryEdit";

/** Collects the corrected text and the reason for one memory. Sending is the caller's job. */
export function MemoryEditDialog({ target, original, pending, error, onClose, onSubmit, onDraftChange }: {
  target: MemoryRevision | null; original: MemoryRevision | null; pending: boolean; error: string | null;
  onClose: () => void; onSubmit: (input: MemoryEditSubmission) => void;
  onDraftChange?: () => void;
}) {
  useTranslation();
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [withEvidence, setWithEvidence] = useState(false);
  const [message, setMessage] = useState<MessageKey | "">("");
  const close = () => { setText(""); setReason(""); setWithEvidence(false); setMessage(""); onClose(); };
  const submit = () => {
    if (!text.trim() || !reason.trim()) { setMessage("memoryEdit.required"); return; }
    setMessage("");
    onSubmit({ text: text.trim(), reason: reason.trim(), evidenceRefs: withEvidence && original ? original.evidence_refs : [] });
  };
  return <Dialog isOpen={target !== null} onClose={close} title={t("memoryEdit.title")}>
    <div className={Classes.DIALOG_BODY}>
      {original && <p className="muted">{t("labels.originalMemory")}{" "}{original.summary}</p>}
      <p>{t("memoryEdit.notice")}</p>
      <label className="review-note">{t("memoryEdit.content")}<TextArea fill rows={3} aria-label={t("memoryEdit.content")} value={text} maxLength={2000} onChange={event => { onDraftChange?.(); setText(event.target.value); }}/></label>
      <label className="review-note">{t("traceHistory.reason")}<TextArea fill rows={2} aria-label={t("traceHistory.reason")} value={reason} maxLength={500} onChange={event => { onDraftChange?.(); setReason(event.target.value); }}/></label>
      {original && original.evidence_refs.length > 0 &&
        <Checkbox label={formatMessage("memoryEvidence", original.evidence_refs.length)} checked={withEvidence} onChange={() => { onDraftChange?.(); setWithEvidence(value => !value); }}/>}
      {message && <Callout compact intent="warning" role="alert">{t(message)}</Callout>}
      {error && <Callout compact intent="danger" role="alert">{error}</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>{t("hold.close")}</Button>
      <Button intent="primary" loading={pending} onClick={submit}>{t("memoryEdit.send")}</Button>
    </div></div>
  </Dialog>;
}
