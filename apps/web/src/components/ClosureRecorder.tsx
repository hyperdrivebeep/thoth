import type { MessageKey } from "../i18n/messages";
import { useDraftGeneration, type DraftTicket } from "./useDraftGeneration";
import { message as formatMessage } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, Classes, Dialog, InputGroup, Radio, RadioGroup, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { closuresKey, recordClosure, type ClosureKind } from "../api/judgmentRecords";
import type { Verdict } from "../api/trace";
import { CLOSURE_KINDS, closureNote } from "./judgmentRecordText";

/** Collects what a person decided about a row that was not met, with the document it rests on. The verdict is not changed. */
export function ClosureRecorder({ projectId, verdict, title, onClose }: { projectId: string; verdict: Verdict | null; title: string; onClose: () => void }) {
  useTranslation();
  const client = useQueryClient();
  const ownership = useDraftGeneration(JSON.stringify([projectId, verdict?.subject_kind, verdict?.subject_id, verdict?.revision_digest]));
  const [kind, setKind] = useState<ClosureKind | "">("");
  const [basis, setBasis] = useState("");
  const [scope, setScope] = useState("");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<MessageKey | "">("");
  const close = () => { ownership.advance(); setKind(""); setBasis(""); setScope(""); setNote(""); setMessage(""); onClose(); };
  const send = useMutation({
    mutationFn: ({ input }: { input: Parameters<typeof recordClosure>[0]; ticket: DraftTicket }) => recordClosure(input),
    onSuccess: async (_result, captured) => { await client.invalidateQueries({ queryKey: closuresKey(projectId) }); if (ownership.isCurrent(captured.ticket)) close(); },
    onError: () => void client.invalidateQueries({ queryKey: closuresKey(projectId) }),
  });
  const submit = () => {
    if (kind === "") { setMessage("closure.choose"); return; }
    if (!basis.trim()) { setMessage("closure.basisNeeded"); return; }
    if (kind === "CONDITION_CHANGED" && !scope.trim()) { setMessage("closure.scopeNeeded"); return; }
    setMessage(""); send.mutate({ ticket: ownership.capture(), input: { projectId, subjectKind: verdict!.subject_kind, subjectId: verdict!.subject_id, kind: kind as ClosureKind, basisRef: basis.trim(),
      note: note.trim(), scope: kind === "CONDITION_CHANGED" ? scope.trim() : "", verdictRevision: verdict!.revision_digest } });
  };
  return <Dialog isOpen={verdict !== null} onClose={close} title={formatMessage("dispositionTitle", title)}>
    <div className={Classes.DIALOG_BODY}>
      <p>{closureNote()}</p>
      <RadioGroup label={t("closure.kind")} selectedValue={kind} onChange={event => { ownership.advance(); setKind(event.currentTarget.value as ClosureKind); }}>
        {CLOSURE_KINDS.map(item => <Radio key={item.code} value={item.code} label={item.label}/>)}
      </RadioGroup>
      {kind !== "" && <p className="muted">{CLOSURE_KINDS.find(item => item.code === kind)?.hint}</p>}
      <label className="review-note">{t("closure.requiredBasis")}<InputGroup fill aria-label={t("closure.basis")} placeholder={t("closure.basisExample")} value={basis} maxLength={500} onChange={event => { ownership.advance(); setBasis(event.target.value); }}/></label>
      {kind === "CONDITION_CHANGED" && <label className="review-note">{t("closure.requiredScope")}<InputGroup fill aria-label={t("closure.scope")} value={scope} maxLength={500} onChange={event => { ownership.advance(); setScope(event.target.value); }}/></label>}
      <label className="review-note">{t("recheck.optionalNote")}<TextArea fill rows={2} aria-label={t("recheck.note")} value={note} maxLength={2000} onChange={event => { ownership.advance(); setNote(event.target.value); }}/></label>
      {message && <Callout compact intent="warning" role="alert">{t(message)}</Callout>}
      {send.error && <Callout compact intent="danger" role="alert">{t("closure.failed")}</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>{t("hold.close")}</Button>
      <Button intent="primary" loading={send.isPending} onClick={submit}>{t("testResult.save")}</Button>
    </div></div>
  </Dialog>;
}
