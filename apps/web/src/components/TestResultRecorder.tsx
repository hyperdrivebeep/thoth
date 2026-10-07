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
    if (matched === "") { setMessage("어느 설명과 맞았는지 고르세요."); return; }
    if (!observation.trim()) { setMessage("관찰한 내용을 적어 주세요."); return; }
    setMessage(""); send.mutate();
  };
  return <div className="test-result">
    {recorded && <p><strong>기록된 결과: {matchLabel(recorded.matched)}</strong> — {recorded.observation}
      <br/><small className="muted">{actorLabel(recorded.actor_id)} · {timeText(recorded.created_at)}{recorded.evidence_refs.length > 0 ? " · 근거 " + recorded.evidence_refs.join(", ") : ""}</small></p>}
    <Button small icon="annotation" onClick={() => setOpen(true)}>{recorded ? "결과 다시 기록" : "결과 기록"}</Button>
    <Dialog isOpen={open} onClose={close} title="시험 결과 기록">
      <div className={Classes.DIALOG_BODY}>
        <p>사람이 한 시험의 결과를 적어 둡니다. 가설은 바뀌지 않고, 기록은 지워지지 않습니다. 같은 시험을 다시 기록하면 가장 최근 기록이 반영됩니다.</p>
        <RadioGroup label="어느 설명과 맞았습니까" selectedValue={matched} onChange={event => setMatched(event.currentTarget.value as MatchKind)}>
          {MATCH_KINDS.map(item => <Radio key={item.code} value={item.code} label={item.label}/>)}
        </RadioGroup>
        <label className="review-note">관찰한 내용 (필수)
          <TextArea fill rows={3} aria-label="관찰한 내용" value={observation} maxLength={2000} onChange={event => setObservation(event.target.value)}/></label>
        <label className="review-note">근거 참조 (선택, 한 줄에 하나)
          <TextArea fill rows={2} aria-label="근거 참조" value={references} onChange={event => setReferences(event.target.value)}/></label>
        {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
        {send.error && <Callout compact intent="danger" role="alert">기록하지 못했습니다. 잠시 뒤 다시 시도해 주세요.</Callout>}
      </div>
      <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
        <Button onClick={close}>닫기</Button>
        <Button intent="primary" loading={send.isPending} onClick={submit}>기록 남기기</Button>
      </div></div>
    </Dialog>
  </div>;
}
