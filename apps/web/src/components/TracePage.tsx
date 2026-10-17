import { message as formatMessage } from "../i18n/formatMessages";
import type { MessageKey } from "../i18n/messages";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout, Tag } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useState } from "react";
import { confirmTraceVerdict, exportTrace, readTrace, traceKey, type ImportApplied, type TraceView, type Verdict, type VerdictChange } from "../api/trace";
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
import { useDraftGeneration, type DraftTicket } from "./useDraftGeneration";

type TraceNotice = { kind: "confirmed" } | { kind: "export"; losses: string[] }
  | { kind: "import"; applied: boolean; lines: { change: VerdictChange; title: string }[] };
function noticeLines(notice: TraceNotice): string[] {
  if (notice.kind === "confirmed") return [t("tracePage.confirmed")];
  if (notice.kind === "export") return [formatMessage("csvDownloaded", notice.losses.map(lossText).join(" / "))];
  const lines = notice.lines.map(({ change, title }) => verdictChangeSentence(change, () => title));
  return [notice.applied ? t("tracePage.applied") : t("tracePage.unchanged"), ...(lines.length ? [t("labels.changedVerdicts"), ...lines] : [])];
}

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
  if (!verdict) return <><td>{t("tracePage.notComputed")}</td><td>—</td><td>—</td><td>—</td></>;
  const look = verdictLook(kind, verdict.state);
  const confirmed = verdict.confirmations[verdict.confirmations.length - 1];
  const stale = verdict.currentness.state === "STALE_BASIS";
  const tags = reasonTags(kind === "REQUIREMENT" ? "REQUIREMENT" : "CRITERION", verdict, view);
  const next = nextStep(kind === "REQUIREMENT" ? "REQUIREMENT" : "CRITERION", verdict.state, stale, tags.map(tag => tag.id));
  return <>
    <td><Tag minimal intent={look.intent} icon={look.icon}>{look.text}</Tag>
      {verdict.reasons.computed.length > 0 && <ul className="trace-reasons">{verdict.reasons.computed.map(code => <li key={code}>{reasonSentence(code, nameOf, verdict.conditions.condition)}</li>)}</ul>}
      {tags.length > 0 && <div className="trace-tags" role="group" aria-label={t("tracePage.reasons")}>{tags.map(tag => <Tag key={tag.id} minimal title={tag.hint}>{tag.label}</Tag>)}</div>}
      {next && <div className="trace-next" role="note"><span className="trace-next-label">{t("tracePage.nextCheck")}</span><ul className="trace-reasons">{next.lines.map(line => <li key={line.text}>{line.text}{line.decider && <><br/><small className="muted">{t("labels.decider")}{" "}{line.decider}</small></>}</li>)}</ul></div>}
      {investigation && onInvestigate && <div><Button small minimal icon="search" className="trace-investigate" aria-label={formatMessage("investigateRow", label)}
        title={t("tracePage.investigateHint")} onClick={() => onInvestigate(investigation)}>{t("tracePage.investigate")}</Button>
        {staleHypotheses > 0 && <Tag minimal intent="warning" title={t("tracePage.staleHint")}>{formatMessage("staleHypotheses", staleHypotheses)}</Tag>}</div>}
      <ClosureMark row={closure} verdict={verdict} label={label} onRecord={onClose}/></td>
    <td>{stale ? <><Tag minimal intent="warning" icon="refresh">{t("tracePage.stale")}</Tag>
      <ul className="trace-reasons">{verdict.currentness.changed_dependencies.map((change, index) => <li key={index}>{changeSentence(change, lookup)}</li>)}</ul></>
      : <Tag minimal intent="success" icon="tick">{t("tracePage.current")}</Tag>}</td>
    <td>{confirmed ? <span>{actorLabel(confirmed.actor_id)}{" "}{t("labels.confirmed")}{" "}{timeText(confirmed.confirmed_at)}<br/><small className="muted">{confirmed.rationale}</small></span> : <span className="muted">{t("tracePage.unconfirmed")}</span>}
      <div><Button small minimal icon="endorsed" aria-label={formatMessage("confirmRow", label)} onClick={() => onConfirm(verdict)}>{t("tracePage.confirm")}</Button></div></td>
    <td>{verdict.basis.source_span_refs.length === 0 ? <span className="muted">{t("tracePage.noLocations")}</span>
      : verdict.basis.source_span_refs.map((ref, index) => <Button key={ref} small minimal icon="document-open" aria-label={formatMessage("rowEvidence", label, index + 1)} onClick={() => onEvidence(ref)}>{t("tracePage.location")}{" "}{index + 1}</Button>)}</td>
  </>;
}

function groupSummary(rows: CriterionRow[]) {
  const states = rows.map(row => row.verdict?.state);
  return formatMessage("criterionCounts", rows.length, states.filter(state => state === "PASS_COMPUTED").length, states.filter(state => state === "FAIL_COMPUTED").length, states.filter(state => !state || state.startsWith("HOLD")).length);
}

/** `onInvestigate` is given by the workspace that can open a conversation; `focusRow` marks the row a result came from. */
export function TracePage({ projectId, onInvestigate, focusRow = null, responseLanguage }: {
  responseLanguage?: "ko" | "en" | null; projectId: string; onInvestigate?: (item: RowInvestigation) => void; focusRow?: { kind: string; id: string } | null;
}) {
  useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: traceKey(projectId), queryFn: ({ signal }) => readTrace(projectId, signal) });
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState<Verdict | null>(null);
  const [closing, setClosing] = useState<Verdict | null>(null);
  const [evidence, setEvidence] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState<TraceNotice | null>(null);
  const [problem, setProblem] = useState<MessageKey | null>(null);
  const ownership = useDraftGeneration(projectId);
  const linked = useHypothesisLinks(projectId, Boolean(onInvestigate));
  const closures = useTraceClosures(projectId);
  const confirm = useMutation({
    mutationFn: (input: { rationale: string; revision: string; expectedDigest: string | null; ticket: DraftTicket }) => confirmTraceVerdict(projectId, input.revision, input.rationale, input.expectedDigest),
    onSuccess: (view, input) => {
      if (ownership.isCurrent(input.ticket)) { ownership.advance(); client.setQueryData(traceKey(projectId), view); setConfirming(null); setNotice({ kind: "confirmed" }); }
      else void client.invalidateQueries({ queryKey: traceKey(projectId) });
    },
    onError: () => void client.invalidateQueries({ queryKey: traceKey(projectId) }),
  });
  const loaded = Boolean(query.data);
  useEffect(() => {
    if (!focusRow || !loaded) return;
    const key = `${focusRow.kind}:${focusRow.id}`;
    Array.from(document.querySelectorAll("[data-trace-row]")).find(row => row.getAttribute("data-trace-row") === key)?.scrollIntoView?.({ block: "center" });
  }, [focusRow, loaded]);
  if (query.isPending) return <p className="muted" role="status">{t("tracePage.loading")}</p>;
  if (query.error || !query.data) return <Callout intent="warning" role="alert">{t("tracePage.failed")}<Button small onClick={() => void query.refetch()}>{t("workspace.reload")}</Button></Callout>;
  const view: TraceView = query.data;
  const name = titleOf(view);
  const groups = buildGroups(view);
  const toggle = (key: string) => setOpen(current => { const next = new Set(current); if (!next.delete(key)) next.add(key); return next; });
  const exportCsv = async () => {
    setProblem(null);
    try {
      const file = await exportTrace(projectId);
      download(file.filename, file.csv_text);
      setNotice({ kind: "export", losses: file.loss_manifest });
    } catch { setProblem("tracePage.exportFailed"); }
  };
  const applied = (result: ImportApplied) => {
    setImporting(false);
    if (result.trace) client.setQueryData(traceKey(projectId), result.trace); else void client.invalidateQueries({ queryKey: traceKey(projectId) });
    void client.invalidateQueries({ queryKey: hypothesisLinksKey(projectId) });
    const title = result.trace ? titleOf(result.trace) : name;
    setNotice({ kind: "import", applied: result.applied, lines: result.applied ? result.verdict_changes.map(change => ({ change, title: title(change.subject_id) })) : [] });
  };
  const cells = (kind: string, verdict: Verdict | undefined, label: string) =>
    <VerdictCells lookup={view} view={view} kind={kind} verdict={verdict} label={label} titleOf={name} onConfirm={next => { ownership.advance(); confirm.reset(); setConfirming(next); }} onEvidence={setEvidence}
      investigation={onInvestigate && verdict ? investigationFor(projectId, kind, verdict, view, responseLanguage) : null} onInvestigate={onInvestigate}
      staleHypotheses={verdict ? staleCount(linked.links, kind, verdict.subject_id) : 0}
      closure={verdict ? closures.closures?.find(item => item.subject_kind === kind && item.subject_id === verdict.subject_id) : undefined} onClose={setClosing}/>;
  const rowMark = (kind: string, id: string, base: string) => ({ "data-trace-row": `${kind}:${id}`,
    className: focusRow?.kind === kind && focusRow.id === id ? `${base} trace-focus` : base });
  const expander = (key: string, label: string) => {
    const isOpen = open.has(key);
    return <><td><Button small minimal icon={isOpen ? "chevron-down" : "chevron-right"} aria-expanded={isOpen} aria-controls={`trace-history-${key}`}
      aria-label={formatMessage("rowHistory", label, isOpen ? t("tracePage.collapse") : t("tracePage.expand"))} onClick={() => toggle(key)}/></td></>;
  };
  return <section className="trace-page" aria-label={t("workspace.trace")}>
    {isSyntheticTrace(view) && <Callout className="trace-synthetic-banner" intent="warning" icon="info-sign" role="note">
      <strong>{t("tracePage.synthetic")}</strong>{" "}{t("tracePage.syntheticNotice")}<br/><small>{SYNTHETIC_NOTICE}</small></Callout>}
    <div className="trace-body">
    <header className="workspace-heading"><div><h1>{t("workspace.trace")}</h1>
      <p>{t("tracePage.explanation")}</p></div>
      <div className="trace-actions"><Button icon="export" onClick={() => void exportCsv()}>{t("tracePage.export")}</Button>
        <Button icon="import" onClick={() => setImporting(true)}>{t("tracePage.import")}</Button></div></header>
    {problem && <Callout intent="danger" role="alert">{t(problem)}</Callout>}
    {notice && <Callout intent="success" role="status" className="trace-notice">{noticeLines(notice).map(line => <p key={line}>{line}</p>)}</Callout>}
    {groups.length === 0 ? <Callout role="status">{t("tracePage.empty")}</Callout> :
      <table className="bp6-html-table trace-table">
        <caption className="trace-sr-only">{t("tracePage.tableLabel")}</caption>
        <colgroup><col style={{ width: "5%" }}/><col style={{ width: "24%" }}/><col style={{ width: "14%" }}/><col style={{ width: "21%" }}/><col style={{ width: "11%" }}/><col style={{ width: "12%" }}/><col style={{ width: "13%" }}/></colgroup>
        <thead><tr><th scope="col"><span className="trace-sr-only">{t("tracePage.history")}</span></th><th scope="col">{t("tracePage.subject")}</th><th scope="col">{t("tracePage.selectedResult")}</th>
          <th scope="col">{t("tracePage.verdict")}</th><th scope="col">{t("tracePage.currentness")}</th><th scope="col">{t("tracePage.humanConfirmation")}</th><th scope="col">{t("trace.field.sourceRefs")}</th></tr></thead>
        {groups.map((group, at) => {
          const groupKey = group.item ? `REQUIREMENT-${group.item.item_id}` : `loose-${at}`;
          return <tbody key={groupKey}>
            <tr {...(group.item ? rowMark("REQUIREMENT", group.item.item_id, "trace-requirement") : { className: "trace-requirement" })}>{group.item ? expander(groupKey, group.item.title || group.item.item_id) : <td/>}
              <th scope="row"><span className="trace-kind">{t("tracePage.requirement")}</span> {group.item ? (group.item.title || group.item.item_id) : t("tracePage.unlinked")}
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
                    <small className="muted">{ruleText(row.rule)}{row.rule ? <> · <Tag minimal>{t("history.condition")}{" "}{describeConditionText(row.rule.condition)}</Tag></> : null}</small></th>
                  <td>{row.chosen.length === 0 ? <span className="muted">{t("tracePage.noSelectedResult")}</span> : row.chosen.map(result => <div key={result.result_id}>{resultText(result)}<br/><small className="muted">{timeText(result.observed_at)}{" "}{t("tracePage.observation")}</small></div>)}</td>
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
      error={confirm.error && ownership.isCurrent(confirm.variables?.ticket) ? t("tracePage.confirmFailed") : null} onClose={() => { ownership.advance(); confirm.reset(); setConfirming(null); }} onDraftChange={ownership.advance}
      onSubmit={rationale => confirming && confirm.mutate({ rationale, revision: confirming.revision_digest, expectedDigest: query.data?.record_digest ?? null, ticket: ownership.capture() })}/>
    <TraceEvidenceDialog projectId={projectId} spanRef={evidence} onClose={() => setEvidence(null)}/>
    <ClosureRecorder projectId={projectId} verdict={closing} title={closing ? name(closing.subject_id) : ""} onClose={() => setClosing(null)}/>
  </section>;
}
