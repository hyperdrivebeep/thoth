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
    if (reason === "") { setMessage("이유를 하나 고르세요."); return; }
    if (needWords && !note.trim()) { setMessage("이 이유는 글로 적어야 합니다."); return; }
    setMessage("");
    onSubmit(covered.map(item => item.hypothesis_id), reason, note.trim(), target.current_verdict_revision);
  };
  const by = distribution?.by_reason ?? {};
  return <Dialog isOpen={target !== null} onClose={close} title="다시 확인">
    <div className={Classes.DIALOG_BODY}>
      {target && <p><strong>{target.statement}</strong></p>}
      <p>사람이 바뀐 판정을 보고 이 가설을 어떻게 볼지 적어 두는 기록입니다. 가설도 판정도 바뀌지 않고, 누가 언제 왜 확인했는지만 남습니다. 기록은 지워지지 않습니다.</p>
      {covered.length > 0 && covered.every(item => item.change === "EVIDENCE_ONLY") && <p className="muted">판정 상태는 그대로이고, 판정이 기댄 결과나 근거 위치만 바뀌었습니다. 바뀐 근거를 직접 본 뒤 이유를 고르세요.</p>}
      <RadioGroup label="이유" selectedValue={reason} onChange={event => setReason(event.currentTarget.value as RecheckReasonCode)}>
        {RECHECK_REASONS.map(item => <Radio key={item.code} value={item.code} label={item.label}/>)}
      </RadioGroup>
      {reason !== "" && <p className="muted">{RECHECK_REASONS.find(item => item.code === reason)?.hint}</p>}
      <label className="review-note">{needWords ? "이유를 글로 적어 주세요 (필수)" : "메모 (선택)"}
        <TextArea fill rows={3} aria-label={needWords ? "이유를 글로 적어 주세요" : "메모"} value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></label>
      {flipped && <p className="muted">충족과 미달이 뒤바뀐 변경이라, 가설을 그대로 두려면 이유를 글로 적어야 합니다.</p>}
      {siblings.length > 0 && <Checkbox checked={withOthers} label={`같은 판정 변경으로 이전 근거 기준이 된 다른 가설 ${siblings.length}개도 함께 확인`} onChange={() => setWithOthers(value => !value)}/>}
      {distribution && distribution.total > 0 && <p className="muted">이 프로젝트에서 지금까지 다시 확인한 이유: 관계없음 {by.UNRELATED ?? 0} · 맞음 {by.STILL_MATCHES ?? 0} · 다시 조사 {by.NEEDS_RESEARCH ?? 0} · 기타 {by.OTHER ?? 0} (그중 충족과 미달이 뒤바뀐 경우 {distribution.flipped}건)</p>}
      {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
      {error && <Callout compact intent="danger" role="alert">{error}</Callout>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>닫기</Button>
      <Button intent="primary" loading={pending} onClick={submit}>확인 기록 남기기</Button>
    </div></div>
  </Dialog>;
}
