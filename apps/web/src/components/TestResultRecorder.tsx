import { t } from "../i18n/locale";
import { Button, Callout, Classes, Dialog, Radio, RadioGroup, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { discriminationKey, recordTestResult, type MatchKind, type TestResult } from "../api/judgmentRecords";
import { MATCH_KINDS, matchLabel } from "./judgmentRecordText";
import { actorLabel, timeText } from "./traceText";

/** The result a person recorded for one test (the latest counts), and the dialog to record or correct it. No model is asked. */
export function TestResultRecorder({ projectId, hypothesisId, testId, recorded }: { projectId: string; hypothesisId: string; testId: string; recorded?: TestResult }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [matched, setMatched] = useState<MatchKind | "">("");
  const [observation, setObservation] = useState("");
  const [references, setReferences] = useState("");
  const [message, setMessage] = useState("");
  const close = () => { setOpen(false); setMatched(""); setObservation(""); setReferences(""); setMessage(""); };
  const send = useMutation({
    mutationFn: () => recordTestResult(projectId, hypothesisId, testId, observation.trim(), matched as MatchKind, references.split("\n").map(line => line.trim()).filter(Boolean)),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: discriminationKey(projectId) }); close(); },
  });
  const submit = () => {
    if (matched === "") { setMessage(t("testResult.choose")); return; }
    if (!observation.trim()) { setMessage(t("testResult.observationNeeded")); return; }
    setMessage(""); send.mutate();
  };
  return <div className="test-result">
    {recorded && <p><strong>{t("labels.recordedResult")}{" "}{matchLabel(recorded.matched)}</strong> — {recorded.observation}
      <br/><small className="muted">{actorLabel(recorded.actor_id)} · {timeText(recorded.created_at)}{recorded.evidence_refs.length > 0 ? " · " + t("history.evidence") + " " + recorded.evidence_refs.join(", ") : ""}</small></p>}
    <Button small icon="annotation" onClick={() => setOpen(true)}>{recorded ? t("testResult.rerecord") : t("testResult.record")}</Button>
    <Dialog isOpen={open} onClose={close} title={t("testResult.title")}>
      <div className={Classes.DIALOG_BODY}>
        <p>{t("testResult.notice")}</p>
        <RadioGroup label={t("testResult.match")} selectedValue={matched} onChange={event => setMatched(event.currentTarget.value as MatchKind)}>
          {MATCH_KINDS.map(item => <Radio key={item.code} value={item.code} label={item.label}/>)}
        </RadioGroup>
        <label className="review-note">{t("testResult.requiredObservation")}<TextArea fill rows={3} aria-label={t("testResult.observation")} value={observation} maxLength={2000} onChange={event => setObservation(event.target.value)}/></label>
        <label className="review-note">{t("testResult.optionalRefs")}<TextArea fill rows={2} aria-label={t("testResult.refs")} value={references} onChange={event => setReferences(event.target.value)}/></label>
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
