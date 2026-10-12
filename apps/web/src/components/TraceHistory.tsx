import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout, Tag } from "@blueprintjs/core";
import { useState } from "react";
import { readTraceHistory, type TraceView, type Verdict, type VerdictRevision } from "../api/trace";
import { resultText, titleOf } from "./traceRows";
import { actorLabel, changeSentence, reasonSentence, resultReasonText, timeText, triggerLabel, verdictLook } from "./traceText";

const policyText = (name: string) => (name === "ALL_MUST_PASS" ? t("traceHistory.all") : t("traceHistory.latest"));

function Revision({ view, revision, parent, current }: { view: TraceView; revision: VerdictRevision; parent: VerdictRevision | undefined; current: boolean }) {
  const look = verdictLook(revision.subject_kind, revision.state);
  const title = titleOf(view);
  const confirmations = view.confirmations.filter(item => item.verdict_revision_digest === revision.revision_digest);
  const selection = revision.selection;
  const found = (id: string, version: number) => view.results.find(item => item.result_id === id && item.result_revision === version);
  return <li className="trace-revision" data-current={current ? "true" : "false"}>
    <h4>{current ? t("traceHistory.current") : t("traceHistory.previous")} · {timeText(revision.actor.computed_at)} · {actorLabel(revision.actor.actor_id)}</h4>
    <p><Tag minimal intent={look.intent} icon={look.icon}>{look.text}</Tag>
      {parent ? <span className="trace-change">{formatMessage("verdictPair", verdictLook(parent.subject_kind, parent.state).text, look.text)}</span>
        : revision.parent_digest === null ? <span className="trace-change">{" "}{t("traceHistory.initial")}</span>
        : <span className="trace-change"> {t("traceHistory.moreNotice")}</span>}</p>
    <p><strong>{t("traceHistory.trigger")}</strong>: {triggerLabel(revision.cause.trigger)}</p>
    {revision.cause.changed.length > 0
      ? <ul className="trace-reasons">{revision.cause.changed.map((change, index) => <li key={index}>{changeSentence(change, view)}</li>)}</ul>
      : <p className="muted">{t("traceHistory.noChanges")}</p>}
    {selection ? <div><strong>{t("tracePage.selectedResult")}</strong> ({policyText(selection.policy.name)})
      {selection.chosen.length === 0 ? <p className="muted">{t("traceHistory.noResult")}</p>
        : <ul className="trace-reasons">{selection.chosen.map(ref => { const result = found(ref.result_id, ref.result_revision);
          return <li key={ref.result_id + ref.result_revision}>{formatMessage("resultRevision", ref.result_id, ref.result_revision)}{result ? `: ${resultText(result)}` : ""}</li>; })}</ul>}
      {selection.excluded.length > 0 && <ul className="trace-reasons">{selection.excluded.map(item => <li key={item.result_id + item.result_revision}>{formatMessage("resultRevision", item.result_id, item.result_revision)}: {resultReasonText(item.reason)}</li>)}</ul>}
    </div> : revision.conditions.required_criteria.length > 0 && <p><strong>{t("traceHistory.requiredCriteria")}</strong>: {revision.conditions.required_criteria.map(title).join(", ")}</p>}
    {revision.reasons.computed.length > 0 && <div><strong>{t("traceHistory.reason")}</strong><ul className="trace-reasons">{revision.reasons.computed.map(code => <li key={code}>{reasonSentence(code, title, revision.conditions.condition)}</li>)}</ul></div>}
    <p><strong>{t("tracePage.humanConfirmation")}</strong>: {confirmations.length === 0 ? t("traceHistory.noConfirmation") : ""}</p>
    {confirmations.length > 0 && <ul className="trace-reasons">{confirmations.map(item => <li key={item.confirmed_at}>{actorLabel(item.actor_id)} · {timeText(item.confirmed_at)} — {item.rationale}</li>)}</ul>}
    <details className="connection-tech"><summary>{t("oauth.technical")}</summary>
      <small>{t("traceHistory.revision")}{" "}{revision.revision_digest}</small>{revision.parent_digest && <small>{t("traceHistory.previousRevision")}{" "}{revision.parent_digest}</small>}
      {revision.reasons.computed.map(code => <small key={code}>{code}</small>)}</details>
  </li>;
}

/** The recent revisions of one verdict, newest first, and older ones on request: each says what changed, why and who confirmed it. */
export function TraceHistory({ view, projectId, verdict }: { view: TraceView; projectId: string; verdict: Verdict | undefined }) {
  const [older, setOlder] = useState<VerdictRevision[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!verdict) return <p className="muted">{t("traceHistory.none")}</p>;
  const shown = [...verdict.recent_history, ...older.filter(item => !verdict.recent_history.some(recent => recent.revision_digest === item.revision_digest))];
  const loadMore = async () => {
    setLoading(true); setFailed(false);
    try {
      const page = await readTraceHistory(projectId, verdict.subject_kind, verdict.subject_id, shown[shown.length - 1].revision_digest);
      setOlder(current => [...current, ...page.revisions]);
    } catch { setFailed(true); }
    finally { setLoading(false); }
  };
  return <div>
    <ol className="trace-history" aria-label={t("traceHistory.title")}>{shown.map((revision, index) =>
      <Revision key={revision.revision_digest} view={view} revision={revision} current={index === 0}
        parent={shown.find(item => item.revision_digest === revision.parent_digest)}/>)}</ol>
    <p className="muted">{formatMessage("verdictHistoryCount", verdict.history_total, shown.length)}</p>
    {failed && <Callout compact intent="warning" role="alert">{t("traceHistory.failed")}</Callout>}
    {shown.length < verdict.history_total && <Button small icon="history" loading={loading} onClick={() => void loadMore()}>{t("traceHistory.more")}</Button>}
  </div>;
}
