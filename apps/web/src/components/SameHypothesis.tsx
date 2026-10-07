import { Button, Callout, Radio, RadioGroup, Tag, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { HypothesisLink } from "../api/hypothesisLink";
import { recordSame, sameKey, type SameView } from "../api/hypothesisSame";
import type { DiscriminationItem } from "../api/judgmentRecords";
import { lessonsKey } from "../api/lessons";
import { standingLine } from "./judgmentRecordText";
import { candidateLabel, memberLine, NO_RESULTS, SAME_BUTTON, SAME_NOTE, sameCandidates, sameRefusal } from "./sameHypothesisText";

/**
 * Hypotheses of other investigations that a person marked as the same as this one, and the controls to mark or take back.
 * The earlier results are shown as a reference; they are not added to this hypothesis's elimination or to the test order.
 */
export function SameHypothesis({ projectId, hypothesisId, same, candidates, discrimination }: {
  projectId: string; hypothesisId: string; same: SameView | undefined; candidates: HypothesisLink[] | undefined; discrimination?: DiscriminationItem[];
}) {
  const client = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [choice, setChoice] = useState("");
  const [note, setNote] = useState("");
  const group = same?.groups.find(item => item.includes(hypothesisId)) ?? [];
  const others = group.filter(id => id !== hypothesisId);
  // A mark is taken back from the pair it was made on; a hypothesis joined only through a third one has no mark of its own here.
  const direct = (id: string) => (same?.pairs ?? []).some(pair => pair.length === 2 && pair.includes(hypothesisId) && pair.includes(id));
  const options = sameCandidates(hypothesisId, candidates, same);
  const reset = () => { setPicking(false); setChoice(""); setNote(""); };
  const send = useMutation({
    mutationFn: (input: { other: string; action: "LINK" | "UNLINK"; memo: string }) => recordSame(projectId, [hypothesisId, input.other], input.action, input.memo),
    onSuccess: async () => { await Promise.all([sameKey(projectId), lessonsKey(projectId)].map(queryKey => client.invalidateQueries({ queryKey }))); reset(); },
  });
  const results = (id: string) => standingLine(discrimination?.find(item => item.hypothesis_id === id)?.standing) ?? NO_RESULTS;
  return <div className="same-hypothesis">
    {others.length > 0 && <div className="same-group">
      <Tag minimal intent="primary">같은 가설로 표시된 다른 조사</Tag>
      <ul>{others.map(id => <li key={id}>{same && memberLine(same, id)}<br/><small className="muted">{results(id)}</small>
        {direct(id)
          ? <>{" "}<Button small minimal icon="disable" disabled={send.isPending} onClick={() => send.mutate({ other: id, action: "UNLINK", memo: "" })}>연결 끊기</Button></>
          : <small className="muted"> 다른 가설을 거쳐 이어져 있습니다.</small>}</li>)}</ul>
      <small className="muted">{SAME_NOTE}</small>
    </div>}
    {!picking && options.length > 0 && <Button small minimal icon="link" onClick={() => setPicking(true)}>{SAME_BUTTON}</Button>}
    {picking && <div className="same-picker">
      <RadioGroup label="이 가설과 같은 가설을 고르세요(같은 추적표 줄이 위에 있습니다)" selectedValue={choice} onChange={event => setChoice(event.currentTarget.value)}>
        {options.map(item => <Radio key={item.hypothesis_id} value={item.hypothesis_id} label={candidateLabel(item)}/>)}
      </RadioGroup>
      <label className="review-note">메모 (선택)
        <TextArea fill rows={2} aria-label="메모" value={note} maxLength={500} onChange={event => setNote(event.target.value)}/></label>
      <Button small intent="primary" disabled={choice === ""} loading={send.isPending} onClick={() => send.mutate({ other: choice, action: "LINK", memo: note })}>표시하기</Button>
      {" "}<Button small minimal onClick={reset}>닫기</Button>
    </div>}
    {send.error && <Callout compact intent="warning" role="alert">{sameRefusal(send.error.message)}</Callout>}
  </div>;
}
