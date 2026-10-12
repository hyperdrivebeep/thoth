import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
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
      <span>{pair.beforeName ?? t("criteriaChange.none")}</span>{pair.beforeDigest && <small title={pair.beforeDigest}> ({shortDigest(pair.beforeDigest)}…)</small>}
      {" → "}<span>{pair.afterName ?? t("criteriaChange.none")}</span>{pair.afterDigest && <small title={pair.afterDigest}> ({shortDigest(pair.afterDigest)}…)</small>}</p>)}
    {change.words.length === 0 && change.attachments.length === 0 && <p>{change.before || t("criteriaChange.none")} → {change.after || t("criteriaChange.none")}</p>}
  </article>;
}

function FullContent({ payload }: { payload: Record<string, unknown> }) {
  const content = objectValue(payload.content);
  const attachments = objectList(content.attachments).map(item => textValue(item.name)).filter(Boolean);
  const rows: [string, string][] = [
    [t("approval.kind"), textValue(payload.action_kind)], [t("approval.route"), textValue(payload.channel)], [t("approval.body"), textValue(content.body_text)],
    [t("approval.attachments"), attachments.join(", ")], [t("approval.disclosure"), listOf(payload.disclosed_data).join(", ")],
    [t("approval.role"), listOf(payload.execution_roles).join(", ")], [t("approval.risk"), statusLabel(textValue(payload.risk_tier)) ?? textValue(payload.risk_tier)],
  ];
  return <dl className="approval-full">{rows.filter(([, text]) => text).map(([label, text]) => <div key={label}><dt>{label}</dt><dd>{text}</dd></div>)}</dl>;
}

/** An approval, with what changed since it was given shown first and the full content folded away. */
export function ApprovalCard({ approval, onReapprove, onDecline }: { approval: Approval; onReapprove?: () => void; onDecline?: () => void }) {
  const [open, setOpen] = useState(false);
  const stale = approval.state === "STALE";
  return <section className="detail-card approval-card" aria-label={t("approval.state")}>
    <div className="badge-row"><Tag minimal intent={stale ? "danger" : approval.state === "APPROVED" ? "success" : "none"}>{stateLabels[approval.state] ?? approval.state}</Tag></div>
    {stale && <Callout compact intent="warning" title={formatMessage("approvalChanges", approval.changes.map(change => change.label).join(" · ") || t("trace.field.content"))}>
      {approval.changes.map(change => <ChangeView key={change.field} change={change}/>)}
      <p className="muted">{t("approval.previous")}</p>
    </Callout>}
    {!stale && approval.reviewNeeded && <Callout compact intent="primary">{t("approval.basisChanged")}</Callout>}
    <Button small minimal icon={open ? "chevron-up" : "chevron-down"} aria-expanded={open} onClick={() => setOpen(value => !value)}>{t("approval.full")}</Button>
    <Collapse isOpen={open}><FullContent payload={approval.payload}/></Collapse>
    {stale && (onReapprove || onDecline) && <div className="approval-actions">
      {onReapprove && <Button intent="primary" onClick={onReapprove}>{t("approval.recreate")}</Button>}
      {onDecline && <Button onClick={onDecline}>{t("approval.notSent")}</Button>}
    </div>}
  </section>;
}
