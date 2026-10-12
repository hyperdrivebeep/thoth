import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout, Classes, Dialog, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { discriminationKey, recordRefutationConditions, type DiscriminationItem } from "../api/judgmentRecords";
import { eliminationNote, eliminationLine } from "./judgmentRecordText";

const MAX = 10;

/** What a person wrote would show the hypothesis wrong, and the elimination mark its recorded test results amount to. */
export function RefutationConditionsPanel({ projectId, hypothesisId, item }: { projectId: string; hypothesisId: string; item?: DiscriminationItem }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [message, setMessage] = useState("");
  const conditions = item?.refutation_conditions ?? [];
  const mark = eliminationLine(item?.elimination ?? null);
  const close = () => { setOpen(false); setMessage(""); };
  const lines = () => text.split("\n").map(line => line.trim()).filter(Boolean);
  const send = useMutation({
    mutationFn: () => recordRefutationConditions(projectId, hypothesisId, lines()),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: discriminationKey(projectId) }); close(); },
  });
  const submit = () => {
    if (lines().length > MAX) { setMessage(formatMessage("maxRefutations", MAX)); return; }
    setMessage(""); send.mutate();
  };
  return <div className="refutation-conditions">
    {mark && <Callout compact intent="none" role="note" className="elimination-mark"><strong>{mark}</strong><br/><small className="muted">{eliminationNote()}</small></Callout>}
    <h4>{t("refutation.conditions")}</h4>
    {conditions.length === 0 ? <p className="muted">{t("refutation.empty")}</p> : <ul>{conditions.map((line, index) => <li key={index}>{line}</li>)}</ul>}
    <Button small icon="edit" onClick={() => { setText(conditions.join("\n")); setOpen(true); }}>{t("refutation.record")}</Button>
    <Dialog isOpen={open} onClose={close} title={t("refutation.record")}>
      <div className={Classes.DIALOG_BODY}>
        <p>{t("refutation.notice")}</p>
        <TextArea fill rows={5} aria-label={t("refutation.conditions")} value={text} onChange={event => setText(event.target.value)}/>
        {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
        {send.error && <Callout compact intent="danger" role="alert">{t("testResult.failed")}</Callout>}
      </div>
      <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
        <Button onClick={close}>{t("hold.close")}</Button>
        <Button intent="primary" loading={send.isPending} onClick={submit}>{t("testResult.save")}</Button>
      </div></div>
    </Dialog>
  </div>;
}
