import { uiText } from "../i18n/locale";
import { t } from "../i18n/locale";
import { Button, Callout, Radio, RadioGroup, Tag, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { HypothesisLink } from "../api/hypothesisLink";
import { recordSame, sameKey, type SameView } from "../api/hypothesisSame";
import type { DiscriminationItem } from "../api/judgmentRecords";
import { lessonsKey } from "../api/lessons";
import { standingLine } from "./judgmentRecordText";
import { candidateLabel, memberLine, NO_RESULTS, SAME_BUTTON, SAME_NOTE, sameCandidates, sameRefusal } from "./sameHypothesisText";
import { useDraftGeneration, type DraftTicket } from "./useDraftGeneration";

/**
 * Hypotheses of other investigations that a person marked as the same as this one, and the controls to mark or take back.
 * The earlier results are shown as a reference; they are not added to this hypothesis's elimination or to the test order.
 */
export function SameHypothesis({ projectId, hypothesisId, same, candidates, discrimination }: {
  projectId: string; hypothesisId: string; same: SameView | undefined; candidates: HypothesisLink[] | undefined; discrimination?: DiscriminationItem[];
}) {
  const client = useQueryClient();
  const ownership = useDraftGeneration(JSON.stringify([projectId, hypothesisId]));
  const [picking, setPicking] = useState(false);
  const [choice, setChoice] = useState("");
  const [note, setNote] = useState("");
  const group = same?.groups.find(item => item.includes(hypothesisId)) ?? [];
  const others = group.filter(id => id !== hypothesisId);
  // A mark is taken back from the pair it was made on; a hypothesis joined only through a third one has no mark of its own here.
  const direct = (id: string) => (same?.pairs ?? []).some(pair => pair.length === 2 && pair.includes(hypothesisId) && pair.includes(id));
  const options = sameCandidates(hypothesisId, candidates, same);
  const reset = () => { ownership.advance(); setPicking(false); setChoice(""); setNote(""); };
  const send = useMutation({
    mutationFn: (input: { other: string; action: "LINK" | "UNLINK"; memo: string; ticket?: DraftTicket }) => recordSame(projectId, [hypothesisId, input.other], input.action, input.memo),
    onSuccess: async (_result, input) => { await Promise.all([sameKey(projectId), lessonsKey(projectId)].map(queryKey => client.invalidateQueries({ queryKey }))); if (ownership.isCurrent(input.ticket)) reset(); },
  });
  const results = (id: string) => standingLine(discrimination?.find(item => item.hypothesis_id === id)?.standing) ?? uiText(NO_RESULTS);
  return <div className="same-hypothesis">
    {others.length > 0 && <div className="same-group">
      <Tag minimal intent="primary">{t("sameHypothesis.otherResearch")}</Tag>
      <ul>{others.map(id => <li key={id}>{same && memberLine(same, id)}<br/><small className="muted">{results(id)}</small>
        {direct(id)
          ? <>{" "}<Button small minimal icon="disable" disabled={send.isPending} onClick={() => send.mutate({ other: id, action: "UNLINK", memo: "" })}>{t("sameHypothesis.disconnect")}</Button></>
          : <small className="muted">{" "}{t("sameHypothesis.indirect")}</small>}</li>)}</ul>
      <small className="muted">{uiText(SAME_NOTE)}</small>
    </div>}
    {!picking && options.length > 0 && <Button small minimal icon="link" onClick={() => { ownership.advance(); setPicking(true); }}>{uiText(SAME_BUTTON)}</Button>}
    {picking && <div className="same-picker">
      <RadioGroup label={t("sameHypothesis.choose")} selectedValue={choice} onChange={event => { ownership.advance(); setChoice(event.currentTarget.value); }}>
        {options.map(item => <Radio key={item.hypothesis_id} value={item.hypothesis_id} label={candidateLabel(item)}/>)}
      </RadioGroup>
      <label className="review-note">{t("recheck.optionalNote")}<TextArea fill rows={2} aria-label={t("recheck.note")} value={note} maxLength={500} onChange={event => { ownership.advance(); setNote(event.target.value); }}/></label>
      <Button small intent="primary" disabled={choice === ""} loading={send.isPending} onClick={() => send.mutate({ other: choice, action: "LINK", memo: note, ticket: ownership.capture() })}>{t("sameHypothesis.apply")}</Button>
      {" "}<Button small minimal onClick={reset}>{t("hold.close")}</Button>
    </div>}
    {send.error && <Callout compact intent="warning" role="alert">{sameRefusal(send.error.message)}</Callout>}
  </div>;
}
