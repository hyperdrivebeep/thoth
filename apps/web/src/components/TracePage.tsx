import { Button, Callout, Tag } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useState } from "react";
import { confirmTraceVerdict, exportTrace, readTrace, traceKey, type ImportApplied, type TraceView, type Verdict } from "../api/trace";
import { reasonTags } from "./traceReasonTags";
import { investigationFor, type RowInvestigation } from "./traceInvestigation";
import { hypothesisLinksKey } from "../api/hypothesisLink";
import { staleCount } from "./hypothesisLinkText";
import { useHypothesisLinks } from "./useHypothesisLinks";
import { useTraceClosures } from "./useJudgmentRecords";
import type { ClosureRow } from "../api/judgmentRecords";
import { ClosureMark } from "./ClosureMark";
import { ClosureRecorder } from "./ClosureRecorder";
import { TraceConfirmDialog } from "./TraceConfirmDialog";
import { TraceEvidenceDialog } from "./TraceEvidenceDialog";
import { TraceHistory } from "./TraceHistory";
import { TraceImportDialog } from "./TraceImportDialog";
import { buildGroups, isSyntheticTrace, resultText, ruleText, titleOf, type CriterionRow } from "./traceRows";
import { actorLabel, changeSentence, describeConditionText, lossText, nextStep, reasonSentence, timeText, verdictChangeSentence, verdictLook, type ChangeLookup } from "./traceText";

const SYNTHETIC_NOTICE = "SYNTHETIC DEMO DATA - NOT MEASURED - NOT APPROVED - NOT FOR ENGINEERING USE";

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
  URL.revokeObjectURL(url);
}

type Cells = { lookup: ChangeLookup; view: TraceView; kind: string; verdict: Verdict | undefined; label: string; titleOf: (id: string) => string; onConfirm: (verdict: Verdict) => void; onEvidence: (ref: string) => void;
  investigation: RowInvestigation | null; onInvestigate?: (item: RowInvestigation) => void; staleHypotheses: number; closure: ClosureRow | undefined; onClose: (verdict: Verdict) => void };

/** Verdict, currentness, person's confirmation and evidence positions of one line. */
function VerdictCells({ lookup, view, kind, verdict, label, titleOf: nameOf, onConfirm, onEvidence, investigation, onInvestigate, staleHypotheses, closure, onClose }: Cells) {
  if (!verdict) return <><td>아직 계산되지 않음</td><td>—</td><td>—</td><td>—</td></>;
  const look = verdictLook(kind, verdict.state);
  const confirmed = verdict.confirmations[verdict.confirmations.length - 1];
  const stale = verdict.currentness.state === "STALE_BASIS";
  const tags = reasonTags(kind === "REQUIREMENT" ? "REQUIREMENT" : "CRITERION", verdict, view);
  const next = nextStep(kind === "REQUIREMENT" ? "REQUIREMENT" : "CRITERION", verdict.state, stale, tags.map(tag => tag.id));
  return <>
    <td><Tag minimal intent={look.intent} icon={look.icon}>{look.text}</Tag>
      {verdict.reasons.computed.length > 0 && <ul className="trace-reasons">{verdict.reasons.computed.map(code => <li key={code}>{reasonSentence(code, nameOf, verdict.conditions.condition)}</li>)}</ul>}
      {tags.length > 0 && <div className="trace-tags" role="group" aria-label="세부 이유">{tags.map(tag => <Tag key={tag.id} minimal title={tag.hint}>{tag.label}</Tag>)}</div>}
      {next && <div className="trace-next" role="note"><span className="trace-next-label">다음 확인</span><ul className="trace-reasons">{next.lines.map(line => <li key={line.text}>{line.text}{line.decider && <><br/><small className="muted">결정: {line.decider}</small></>}</li>)}</ul></div>}
      {investigation && onInvestigate && <div><Button small minimal icon="search" className="trace-investigate" aria-label={`${label} 원인 조사`}
        title="이 줄의 조사 대화를 열고 질문을 채웁니다. 보내기를 눌러야 시작합니다." onClick={() => onInvestigate(investigation)}>원인 조사</Button>
        {staleHypotheses > 0 && <Tag minimal intent="warning" title="이 줄의 판정이 바뀌어, 이 줄에서 나온 가설이 이전 근거 기준이 되었습니다. 대화에서 다시 확인할 수 있습니다.">낡은 가설 {staleHypotheses}개</Tag>}</div>}
      <ClosureMark row={closure} verdict={verdict} label={label} onRecord={onClose}/></td>
    <td>{stale ? <><Tag minimal intent="warning" icon="refresh">근거가 바뀜 · 다시 계산 필요</Tag>
      <ul className="trace-reasons">{verdict.currentness.changed_dependencies.map((change, index) => <li key={index}>{changeSentence(change, lookup)}</li>)}</ul></>
      : <Tag minimal intent="success" icon="tick">지금 입력 기준</Tag>}</td>
    <td>{confirmed ? <span>{actorLabel(confirmed.actor_id)} 확인 · {timeText(confirmed.confirmed_at)}<br/><small className="muted">{confirmed.rationale}</small></span> : <span className="muted">아직 확인 없음</span>}
      <div><Button small minimal icon="endorsed" aria-label={`${label} 판정 확인 기록 남기기`} onClick={() => onConfirm(verdict)}>확인</Button></div></td>
    <td>{verdict.basis.source_span_refs.length === 0 ? <span className="muted">근거 위치 없음</span>
      : verdict.basis.source_span_refs.map((ref, index) => <Button key={ref} small minimal icon="document-open" aria-label={`${label} 근거 위치 ${index + 1} 보기`} onClick={() => onEvidence(ref)}>위치 {index + 1}</Button>)}</td>
  </>;
}

function groupSummary(rows: CriterionRow[]) {
  const states = rows.map(row => row.verdict?.state);
  return `기준 ${rows.length}개 · 충족 ${states.filter(state => state === "PASS_COMPUTED").length} · 미달 ${states.filter(state => state === "FAIL_COMPUTED").length} · 보류 ${states.filter(state => !state || state.startsWith("HOLD")).length}`;
}

/** `onInvestigate` is given by the workspace that can open a conversation; `focusRow` marks the row a result came from. */
export function TracePage({ projectId, onInvestigate, focusRow = null }: {
  projectId: string; onInvestigate?: (item: RowInvestigation) => void; focusRow?: { kind: string; id: string } | null;
}) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: traceKey(projectId), queryFn: ({ signal }) => readTrace(projectId, signal) });
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState<Verdict | null>(null);
  const [closing, setClosing] = useState<Verdict | null>(null);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState<string[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const linked = useHypothesisLinks(projectId, Boolean(onInvestigate));
  const closures = useTraceClosures(projectId);
  const confirm = useMutation({
    mutationFn: (rationale: string) => confirmTraceVerdict(projectId, confirming!.revision_digest, rationale, query.data?.record_digest ?? null),
    onSuccess: view => { client.setQueryData(traceKey(projectId), view); setConfirming(null); setNotice(["확인을 기록했습니다. 판정은 바뀌지 않았습니다."]); },
    onError: () => void client.invalidateQueries({ queryKey: traceKey(projectId) }),
  });
  const loaded = Boolean(query.data);
  useEffect(() => {
    if (!focusRow || !loaded) return;
    const key = `${focusRow.kind}:${focusRow.id}`;
    Array.from(document.querySelectorAll("[data-trace-row]")).find(row => row.getAttribute("data-trace-row") === key)?.scrollIntoView?.({ block: "center" });
  }, [focusRow, loaded]);
  if (query.isPending) return <p className="muted" role="status">추적표를 읽는 중…</p>;
  if (query.error || !query.data) return <Callout intent="warning" role="alert">추적표를 읽지 못했습니다. 비어 있다는 뜻이 아닙니다.
    <Button small onClick={() => void query.refetch()}>다시 읽기</Button></Callout>;
  const view: TraceView = query.data;
  const name = titleOf(view);
  const groups = buildGroups(view);
  const toggle = (key: string) => setOpen(current => { const next = new Set(current); if (!next.delete(key)) next.add(key); return next; });
  const exportCsv = async () => {
    setProblem(null);
    try {
      const file = await exportTrace(projectId);
      download(file.filename, file.csv_text);
      setNotice([`CSV 파일을 내려받았습니다. 이 파일에 담기지 않는 것: ${file.loss_manifest.map(lossText).join(" / ")}`]);
    } catch { setProblem("CSV를 내보내지 못했습니다. 잠시 뒤 다시 시도하세요."); }
  };
  const applied = (result: ImportApplied) => {
    setImporting(false);
    if (result.trace) client.setQueryData(traceKey(projectId), result.trace); else void client.invalidateQueries({ queryKey: traceKey(projectId) });
    void client.invalidateQueries({ queryKey: hypothesisLinksKey(projectId) });
    const lines = result.applied ? result.verdict_changes.map(change => verdictChangeSentence(change, result.trace ? titleOf(result.trace) : name)) : [];
    setNotice([result.applied ? "반영했습니다." : "바뀌는 것이 없어 반영하지 않았습니다.", ...(lines.length > 0 ? ["바뀐 판정:", ...lines] : [])]);
  };
  const cells = (kind: string, verdict: Verdict | undefined, label: string) =>
    <VerdictCells lookup={view} view={view} kind={kind} verdict={verdict} label={label} titleOf={name} onConfirm={setConfirming} onEvidence={setEvidence}
      investigation={onInvestigate && verdict ? investigationFor(projectId, kind, verdict, view) : null} onInvestigate={onInvestigate}
      staleHypotheses={verdict ? staleCount(linked.links, kind, verdict.subject_id) : 0}
      closure={verdict ? closures.closures?.find(item => item.subject_kind === kind && item.subject_id === verdict.subject_id) : undefined} onClose={setClosing}/>;
  const rowMark = (kind: string, id: string, base: string) => ({ "data-trace-row": `${kind}:${id}`,
    className: focusRow?.kind === kind && focusRow.id === id ? `${base} trace-focus` : base });
  const expander = (key: string, label: string) => {
    const isOpen = open.has(key);
    return <><td><Button small minimal icon={isOpen ? "chevron-down" : "chevron-right"} aria-expanded={isOpen} aria-controls={`trace-history-${key}`}
      aria-label={`${label} 판정 이력 ${isOpen ? "접기" : "펼치기"}`} onClick={() => toggle(key)}/></td></>;
  };
  return <section className="trace-page" aria-label="추적표">
    {isSyntheticTrace(view) && <Callout className="trace-synthetic-banner" intent="warning" icon="info-sign" role="note">
      <strong>합성 데이터입니다.</strong> 실제로 측정한 값이 아니고, 승인된 값도 아니며, 공학 용도로 쓸 수 없습니다.<br/><small>{SYNTHETIC_NOTICE}</small></Callout>}
    <div className="trace-body">
    <header className="workspace-heading"><div><h1>추적표</h1>
      <p>요구사항이 어떤 기준과 결과로 판정되는지 한 줄씩 보여 줍니다. 판정은 규칙으로 계산한 값이며 모델이 정하지 않습니다.</p></div>
      <div className="trace-actions"><Button icon="export" onClick={() => void exportCsv()}>CSV로 내보내기</Button>
        <Button icon="import" onClick={() => setImporting(true)}>CSV 들여오기</Button></div></header>
    {problem && <Callout intent="danger" role="alert">{problem}</Callout>}
    {notice && <Callout intent="success" role="status" className="trace-notice">{notice.map(line => <p key={line}>{line}</p>)}</Callout>}
    {groups.length === 0 ? <Callout role="status">아직 추적표가 없습니다. 'CSV 들여오기'에서 '새로 만들기'로 파일을 들여오면 요구사항과 기준이 여기에 나타납니다.</Callout> :
      <table className="bp6-html-table trace-table">
        <caption className="trace-sr-only">요구사항별 기준, 고른 결과, 판정, 현재성, 사람 확인, 근거 위치</caption>
        <colgroup><col style={{ width: "5%" }}/><col style={{ width: "24%" }}/><col style={{ width: "14%" }}/><col style={{ width: "21%" }}/><col style={{ width: "11%" }}/><col style={{ width: "12%" }}/><col style={{ width: "13%" }}/></colgroup>
        <thead><tr><th scope="col"><span className="trace-sr-only">이력</span></th><th scope="col">요구사항 / 기준 (조건)</th><th scope="col">고른 결과</th>
          <th scope="col">판정</th><th scope="col">현재성</th><th scope="col">사람 확인</th><th scope="col">근거 위치</th></tr></thead>
        {groups.map((group, at) => {
          const groupKey = group.item ? `REQUIREMENT-${group.item.item_id}` : `loose-${at}`;
          return <tbody key={groupKey}>
            <tr {...(group.item ? rowMark("REQUIREMENT", group.item.item_id, "trace-requirement") : { className: "trace-requirement" })}>{group.item ? expander(groupKey, group.item.title || group.item.item_id) : <td/>}
              <th scope="row"><span className="trace-kind">요구사항</span> {group.item ? (group.item.title || group.item.item_id) : "요구사항에 연결되지 않은 기준"}
                {group.item && <><br/><small className="muted">{group.item.item_id}</small></>}</th>
              <td>{groupSummary(group.criteria)}</td>
              {group.item ? cells("REQUIREMENT", group.verdict, group.item.title || group.item.item_id) : <><td>—</td><td>—</td><td>—</td><td>—</td></>}</tr>
            {group.item && open.has(groupKey) && <tr id={`trace-history-${groupKey}`}><td/><td colSpan={6}><TraceHistory key={group.verdict?.revision_digest} view={view} projectId={projectId} verdict={group.verdict}/></td></tr>}
            {group.criteria.map(row => {
              const key = `CRITERION-${row.item.item_id}`;
              const label = row.item.title || row.item.item_id;
              return <Fragment key={key}>
                <tr {...rowMark("CRITERION", row.item.item_id, "trace-criterion")}>{expander(key, label)}
                  <th scope="row"><span className="trace-indent">{label}</span><br/>
                    <small className="muted">{ruleText(row.rule)}{row.rule ? <> · <Tag minimal>조건 {describeConditionText(row.rule.condition)}</Tag></> : null}</small></th>
                  <td>{row.chosen.length === 0 ? <span className="muted">고른 결과 없음</span> : row.chosen.map(result => <div key={result.result_id}>{resultText(result)}<br/><small className="muted">{timeText(result.observed_at)} 관측</small></div>)}</td>
                  {cells("CRITERION", row.verdict, label)}</tr>
                {open.has(key) && <tr id={`trace-history-${key}`}><td/><td colSpan={6}><TraceHistory key={row.verdict?.revision_digest} view={view} projectId={projectId} verdict={row.verdict}/></td></tr>}
              </Fragment>;
            })}
          </tbody>;
        })}
      </table>}
    </div>
    <TraceImportDialog key={String(groups.length > 0)} projectId={projectId} isOpen={importing} hasTrace={groups.length > 0} onClose={() => setImporting(false)} onApplied={applied}/>
    <TraceConfirmDialog verdict={confirming} title={confirming ? name(confirming.subject_id) : ""} pending={confirm.isPending}
      error={confirm.error ? "확인을 기록하지 못했습니다. 화면을 다시 읽었으니 다시 시도해 주세요." : null} onClose={() => { confirm.reset(); setConfirming(null); }} onSubmit={rationale => confirm.mutate(rationale)}/>
    <TraceEvidenceDialog projectId={projectId} spanRef={evidence} onClose={() => setEvidence(null)}/>
    <ClosureRecorder projectId={projectId} verdict={closing} title={closing ? name(closing.subject_id) : ""} onClose={() => setClosing(null)}/>
  </section>;
}
