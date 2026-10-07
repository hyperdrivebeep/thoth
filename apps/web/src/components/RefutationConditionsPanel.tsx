import { Button, Callout, Classes, Dialog, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { discriminationKey, recordRefutationConditions, type DiscriminationItem } from "../api/judgmentRecords";
import { ELIMINATION_NOTE, eliminationLine } from "./judgmentRecordText";

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
    if (lines().length > MAX) { setMessage("기각 조건은 " + MAX + "개까지 적을 수 있습니다."); return; }
    setMessage(""); send.mutate();
  };
  return <div className="refutation-conditions">
    {mark && <Callout compact intent="none" role="note" className="elimination-mark"><strong>{mark}</strong><br/><small className="muted">{ELIMINATION_NOTE}</small></Callout>}
    <h4>기각 조건</h4>
    {conditions.length === 0 ? <p className="muted">아직 적어 둔 기각 조건이 없습니다.</p> : <ul>{conditions.map((line, index) => <li key={index}>{line}</li>)}</ul>}
    <Button small icon="edit" onClick={() => { setText(conditions.join("\n")); setOpen(true); }}>기각 조건 기록</Button>
    <Dialog isOpen={open} onClose={close} title="기각 조건 기록">
      <div className={Classes.DIALOG_BODY}>
        <p>이런 결과가 나오면 이 가설이 틀렸다고 볼 조건을 한 줄에 하나씩 적습니다. 새로 기록하면 가장 최근 목록이 보이고, 이전 기록은 남습니다.</p>
        <TextArea fill rows={5} aria-label="기각 조건" value={text} onChange={event => setText(event.target.value)}/>
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
