import { Button, Callout, Tag } from "@blueprintjs/core";
import { useState } from "react";
import { readTraceHistory, type TraceView, type Verdict, type VerdictRevision } from "../api/trace";
import { resultText, titleOf } from "./traceRows";
import { actorLabel, changeSentence, reasonSentence, resultReasonText, timeText, triggerLabel, verdictLook } from "./traceText";

const policyText = (name: string) => (name === "ALL_MUST_PASS" ? "모든 결과가 기준을 만족해야 함" : "조건별로 가장 최근 결과");

function Revision({ view, revision, parent, current }: { view: TraceView; revision: VerdictRevision; parent: VerdictRevision | undefined; current: boolean }) {
  const look = verdictLook(revision.subject_kind, revision.state);
  const title = titleOf(view);
  const confirmations = view.confirmations.filter(item => item.verdict_revision_digest === revision.revision_digest);
  const selection = revision.selection;
  const found = (id: string, version: number) => view.results.find(item => item.result_id === id && item.result_revision === version);
  return <li className="trace-revision" data-current={current ? "true" : "false"}>
    <h4>{current ? "현재 판정" : "이전 판정"} · {timeText(revision.actor.computed_at)} · {actorLabel(revision.actor.actor_id)}</h4>
    <p><Tag minimal intent={look.intent} icon={look.icon}>{look.text}</Tag>
      {parent ? <span className="trace-change"> 이전 판정 "{verdictLook(parent.subject_kind, parent.state).text}" → 이번 판정 "{look.text}"</span>
        : revision.parent_digest === null ? <span className="trace-change"> 처음 계산한 판정입니다.</span>
        : <span className="trace-change"> 이 앞의 판정은 "이전 이력 더 보기"로 볼 수 있습니다.</span>}</p>
    <p><strong>계산을 일으킨 일</strong>: {triggerLabel(revision.cause.trigger)}</p>
    {revision.cause.changed.length > 0
      ? <ul className="trace-reasons">{revision.cause.changed.map((change, index) => <li key={index}>{changeSentence(change, view)}</li>)}</ul>
      : <p className="muted">이 판정에 영향을 준 입력 변경은 기록되지 않았습니다.</p>}
    {selection ? <div><strong>고른 결과</strong> ({policyText(selection.policy.name)})
      {selection.chosen.length === 0 ? <p className="muted">고를 수 있는 결과가 없었습니다.</p>
        : <ul className="trace-reasons">{selection.chosen.map(ref => { const result = found(ref.result_id, ref.result_revision);
          return <li key={ref.result_id + ref.result_revision}>{ref.result_id} (개정 {ref.result_revision}){result ? `: ${resultText(result)}` : ""}</li>; })}</ul>}
      {selection.excluded.length > 0 && <ul className="trace-reasons">{selection.excluded.map(item => <li key={item.result_id + item.result_revision}>{item.result_id} (개정 {item.result_revision}): {resultReasonText(item.reason)}</li>)}</ul>}
    </div> : revision.conditions.required_criteria.length > 0 && <p><strong>판정에 쓴 필수 기준</strong>: {revision.conditions.required_criteria.map(title).join(", ")}</p>}
    {revision.reasons.computed.length > 0 && <div><strong>이유</strong><ul className="trace-reasons">{revision.reasons.computed.map(code => <li key={code}>{reasonSentence(code, title, revision.conditions.condition)}</li>)}</ul></div>}
    <p><strong>사람 확인</strong>: {confirmations.length === 0 ? "이 판정에 대한 확인은 없습니다." : ""}</p>
    {confirmations.length > 0 && <ul className="trace-reasons">{confirmations.map(item => <li key={item.confirmed_at}>{actorLabel(item.actor_id)} · {timeText(item.confirmed_at)} — {item.rationale}</li>)}</ul>}
    <details className="connection-tech"><summary>기술 정보</summary>
      <small>판정 개정 {revision.revision_digest}</small>{revision.parent_digest && <small>이전 개정 {revision.parent_digest}</small>}
      {revision.reasons.computed.map(code => <small key={code}>{code}</small>)}</details>
  </li>;
}

/** The recent revisions of one verdict, newest first, and older ones on request: each says what changed, why and who confirmed it. */
export function TraceHistory({ view, projectId, verdict }: { view: TraceView; projectId: string; verdict: Verdict | undefined }) {
  const [older, setOlder] = useState<VerdictRevision[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  if (!verdict) return <p className="muted">아직 계산된 판정이 없습니다.</p>;
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
    <ol className="trace-history" aria-label="판정 이력">{shown.map((revision, index) =>
      <Revision key={revision.revision_digest} view={view} revision={revision} current={index === 0}
        parent={shown.find(item => item.revision_digest === revision.parent_digest)}/>)}</ol>
    <p className="muted">판정 이력 전체 {verdict.history_total}개 중 {shown.length}개를 보고 있습니다.</p>
    {failed && <Callout compact intent="warning" role="alert">이전 이력을 읽지 못했습니다. 이력이 없다는 뜻이 아닙니다.</Callout>}
    {shown.length < verdict.history_total && <Button small icon="history" loading={loading} onClick={() => void loadMore()}>이전 이력 더 보기</Button>}
  </div>;
}
