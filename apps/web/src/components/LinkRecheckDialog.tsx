import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout, Checkbox, Classes, Dialog, Radio, RadioGroup, TextArea } from "@blueprintjs/core";
import { useState } from "react";
import type { ReasonDistribution } from "../api/hypothesisLink";
import { noteRequired, RECHECK_REASONS, type HypothesisLink, type RecheckReasonCode } from "./hypothesisLinkText";

/** Collects the reason a person gives for looking again at a changed verdict. Sending is the caller's job. */
export function LinkRecheckDialog({ target, siblings, distribution, pending, error, onClose, onSubmit }: {
  target: HypothesisLink | null; siblings: HypothesisLink[]; distribution?: ReasonDistribution; pending: boolean; error: string | null;
  onClose: () => void; onSubmit: (hypothesisIds: string[], reason: RecheckReasonCode, note: string, currentVerdictRevision: string | null) => void;
}) {
  const [reason, setReason] = useState<RecheckReasonCode | "">("");
  const [note, setNote] = useState("");
  const [withOthers, setWithOthers] = useState(true);
  const [message, setMessage] = useState("");
  const close = () => { setReason(""); setNote(""); setMessage(""); setWithOthers(true); onClose(); };
  const covered = target ? [target, ...(withOthers ? siblings : [])] : [];
  const flipped = covered.some(item => item.change === "FLIPPED");
  const needWords = reason !== "" && noteRequired(reason, flipped);
  const submit = () => {
    if (!target) return;
    if (reason === "") { setMessage(t("recheck.reasonNeeded")); return; }
    if (needWords && !note.trim()) { setMessage(t("recheck.writtenNeeded")); return; }
    setMessage("");
    onSubmit(covered.map(item => item.hypothesis_id), reason, note.trim(), target.current_verdict_revision);
  };
  const by = distribution?.by_reason ?? {};
  return <Dialog isOpen={target !== null} onClose={close} title={t("workspace.checkAgain")}>
    <div className={Classes.DIALOG_BODY}>
      {target && <p><strong>{target.statement}</strong></p>}
      <p>{t("recheck.notice")}</p>
      {covered.length > 0 && covered.every(item => item.change === "EVIDENCE_ONLY") && <p className="muted">{t("recheck.basisOnly")}</p>}
      <RadioGroup label={t("traceHistory.reason")} selectedValue={reason} onChange={event => setReason(event.currentTarget.value as RecheckReasonCode)}>
        {RECHECK_REASONS.map(item => <Radio key={item.code} value={item.code} label={item.label}/>)}
      </RadioGroup>
      {reason !== "" && <p className="muted">{RECHECK_REASONS.find(item => item.code === reason)?.hint}</p>}
      <label className="review-note">{needWords ? t("recheck.writtenLabel") : t("recheck.optionalNote")}
        <TextArea fill rows={3} aria-label={needWords ? t("recheck.writeReason") : t("recheck.note")} value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></label>
      {flipped && <p className="muted">{t("recheck.flipped")}</p>}
      {siblings.length > 0 && <Checkbox checked={withOthers} label={formatMessage("recheckSiblings", siblings.length)} onChange={() => setWithOthers(value => !value)}/>}
      {distribution && distribution.total > 0 && <p className="muted">{formatMessage("recheckDistribution", by.UNRELATED ?? 0, by.STILL_MATCHES ?? 0, by.NEEDS_RESEARCH ?? 0, by.OTHER ?? 0, distribution.flipped)}</p>}
      {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
      {error && <Callout compact intent="danger" role="alert">{error}</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>{t("hold.close")}</Button>
      <Button intent="primary" loading={pending} onClick={submit}>{t("traceConfirm.record")}</Button>
    </div></div>
  </Dialog>;
}
