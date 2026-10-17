import type { MessageKey } from "../i18n/messages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, Classes, Dialog, TextArea } from "@blueprintjs/core";
import { useState } from "react";
import { verdictLook } from "./traceText";
import type { Verdict } from "../api/trace";

/** Collects the reason for a person's confirmation. Sending is the caller's job; a confirmation never changes the verdict. */
export function TraceConfirmDialog({ verdict, title, pending, error, onClose, onSubmit, onDraftChange }: {
  verdict: Verdict | null; title: string; pending: boolean; error: string | null; onClose: () => void; onSubmit: (rationale: string) => void;
  onDraftChange?: () => void;
}) {
  useTranslation();
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<MessageKey | "">("");
  const close = () => { setReason(""); setMessage(""); onClose(); };
  const submit = () => { if (!reason.trim()) { setMessage("traceConfirm.reasonNeeded"); return; } setMessage(""); onSubmit(reason.trim()); };
  return <Dialog isOpen={verdict !== null} onClose={close} title={t("traceConfirm.title")}>
    <div className={Classes.DIALOG_BODY}>
      {verdict && <p><strong>{title}</strong>: {verdictLook(verdict.subject_kind, verdict.state).text}</p>}
      <p>{t("traceConfirm.notice")}</p>
      <label className="review-note">{t("traceConfirm.reason")}<TextArea fill rows={3} aria-label={t("traceConfirm.reason")} value={reason} maxLength={2000} onChange={event => { onDraftChange?.(); setReason(event.target.value); }}/></label>
      {message && <Callout compact intent="warning" role="alert">{t(message)}</Callout>}
      {error && <Callout compact intent="danger" role="alert">{error}</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>{t("hold.close")}</Button>
      <Button intent="primary" loading={pending} onClick={submit}>{t("traceConfirm.record")}</Button>
    </div></div>
  </Dialog>;
}
