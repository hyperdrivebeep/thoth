import { Button, Callout, Collapse, Tag } from "@blueprintjs/core";
import { useState } from "react";
import { objectList, objectValue, textValue } from "../api/presentation";
import { compactWords, listOf, shortDigest, stateLabels, type Approval, type ApprovalChange } from "./approvalView";
import { statusLabel } from "./statusLabels";

function ChangeView({ change }: { change: ApprovalChange }) {
  return <article className="approval-change">
    <h5>{change.label}</h5>
    {change.words.length > 0 && <p className="approval-words">{compactWords(change.words).map((part, index) =>
      part.op === "del" ? <span key={index}><del>{part.text}</del>{" "}</span> : part.op === "add" ? <span key={index}><ins>{part.text}</ins>{" "}</span>
        : part.op === "gap" ? <span key={index} className="muted">… </span> : <span key={index}>{part.text} </span>)}</p>}
    {change.attachments.map((pair, index) => <p key={index}>
      <span>{pair.beforeName ?? "없음"}</span>{pair.beforeDigest && <small title={pair.beforeDigest}> ({shortDigest(pair.beforeDigest)}…)</small>}
      {" → "}<span>{pair.afterName ?? "없음"}</span>{pair.afterDigest && <small title={pair.afterDigest}> ({shortDigest(pair.afterDigest)}…)</small>}</p>)}
    {change.words.length === 0 && change.attachments.length === 0 && <p>{change.before || "없음"} → {change.after || "없음"}</p>}
  </article>;
}

function FullContent({ payload }: { payload: Record<string, unknown> }) {
  const content = objectValue(payload.content);
  const attachments = objectList(content.attachments).map(item => textValue(item.name)).filter(Boolean);
  const rows: [string, string][] = [
    ["행동 종류", textValue(payload.action_kind)], ["전달 경로", textValue(payload.channel)], ["본문", textValue(content.body_text)],
    ["첨부", attachments.join(", ")], ["공개되는 정보", listOf(payload.disclosed_data).join(", ")],
    ["실행 권한 역할", listOf(payload.execution_roles).join(", ")], ["위험 등급", statusLabel(textValue(payload.risk_tier)) ?? textValue(payload.risk_tier)],
  ];
  return <dl className="approval-full">{rows.filter(([, text]) => text).map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}</dl>;
}

/** An approval, with what changed since it was given shown first and the full content folded away. */
export function ApprovalCard({ approval, onReapprove, onDecline }: { approval: Approval; onReapprove?: () => void; onDecline?: () => void }) {
  const [open, setOpen] = useState(false);
  const stale = approval.state === "STALE";
  return <section className="detail-card approval-card" aria-label="승인 상태">
    <div className="badge-row"><Tag minimal intent={stale ? "danger" : approval.state === "APPROVED" ? "success" : "none"}>{stateLabels[approval.state] ?? approval.state}</Tag></div>
    {stale && <Callout compact intent="warning" title={`승인 뒤 바뀜: ${approval.changes.map(change => change.label).join(" · ") || "내용"}`}>
      {approval.changes.map(change => <ChangeView key={change.field} change={change}/>)}
      <p className="muted">이전 승인은 바뀌기 전 버전에 유효했음 (승인 기록은 그대로 남아 있습니다).</p>
    </Callout>}
    {!stale && approval.reviewNeeded && <Callout compact intent="primary">근거가 바뀌어 다시 살펴보는 것이 좋습니다. 보낼 내용은 그대로이므로 승인은 유지됩니다.</Callout>}
    <Button small minimal icon={open ? "chevron-up" : "chevron-down"} aria-expanded={open} onClick={() => setOpen(value => !value)}>전체 내용 보기</Button>
    <Collapse isOpen={open}><FullContent payload={approval.payload}/></Collapse>
    {stale && (onReapprove || onDecline) && <div className="approval-actions">
      {onReapprove && <Button intent="primary" onClick={onReapprove}>바뀐 내용으로 승인 요청 다시 만들기</Button>}
      {onDecline && <Button onClick={onDecline}>보내지 않음</Button>}
    </div>}
  </section>;
}
