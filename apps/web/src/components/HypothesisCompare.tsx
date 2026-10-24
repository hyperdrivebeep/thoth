import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, HTMLTable, Tag } from "@blueprintjs/core";
import { useState } from "react";
import { hypothesisRows, selectedEvidence, type Relation } from "./hypothesisView";
import { matrixLines, noCounterNote } from "./hypothesisMatrix";
import { causeUnconfirmed } from "./hypothesisReview";
import { CauseUnconfirmed, HypothesisFields } from "./HypothesisFields";
import { HypothesisLinkNotice } from "./HypothesisLinkNotice";
import { TestActionDraft } from "./TestActionDraft";
import { RefutationConditionsPanel } from "./RefutationConditionsPanel";
import { TestResultRecorder } from "./TestResultRecorder";
import { TestOrderSection } from "./TestOrderSection";
import { ExpectedResults } from "./ExpectedResults";
import { GroundingRowsNotice } from "./GroundingNotice";
import { LessonRecall } from "./LessonRecall";
import type { LessonItem } from "../api/lessons";
import { objectList, objectValue } from "../api/presentation";
import type { DiscriminationItem } from "../api/judgmentRecords";
import type { HypothesisLink } from "./hypothesisLinkText";
import type { ReasonDistribution } from "../api/hypothesisLink";
import { counterReviewLabel } from "./statusLabels";
import { parseAnswer } from "./answerText";
import { ReviewStatusLine } from "./ReviewStatusLine";
import { openRequest, type ReviewControls } from "./judgmentReview";
import { standingNote, standingLine } from "./judgmentRecordText";
import type { SameView } from "../api/hypothesisSame";
import { SameHypothesis } from "./SameHypothesis";

const relationLabel: Record<Relation, string> = { get SUPPORT() { return t("hypotheses.support"); }, get COUNTER() { return t("hypotheses.refute"); }, get NOT_APPLICABLE() { return t("hypotheses.notApplicable"); }, get UNEVALUATED() { return t("hypotheses.uncompared"); } };
const relationIntent = { SUPPORT: "success", COUNTER: "danger", NOT_APPLICABLE: "none", UNEVALUATED: "none" } as const;
const standingOf = (items: DiscriminationItem[] | undefined, id: string) => items?.find(item => item.hypothesis_id === id)?.standing;

/** Hypothesis cards and, when the answer's selected sources are known, a source-by-hypothesis relation table. */
export function HypothesisCompare({ result, review, projectId, links, distribution, canDraft, discrimination, lessons, same, onOpenEvidence }: {
  result: Record<string, unknown>; review?: ReviewControls;
  /** Hypotheses that came from a trace row, with what happened to that row's verdict; only a hypothesis with a link gets a mark. */
  projectId?: string; links?: HypothesisLink[]; distribution?: ReasonDistribution;
  /** Show a person's controls on each test and hypothesis (prepare an action request, record a result, refutation conditions); needs the project and a query client. */
  canDraft?: boolean;
  /** What people recorded for the tests of these hypotheses. */
  discrimination?: DiscriminationItem[];
  /** Earlier results recalled for the rows these hypotheses came from; shown as a reference, never used for the order. */
  lessons?: LessonItem[];
  /** Hypotheses of other investigations a person marked as the same one; a reference only, never added to a count. */
  same?: SameView;
  onOpenEvidence?: (spanId: string) => void;
}) {
  const rows = hypothesisRows(result);
  const selected = selectedEvidence(result);
  const [onlyOpen, setOnlyOpen] = useState(true);
  if (rows.length === 0) return <><GroundingRowsNotice result={result}/><p>{t("hypotheses.empty")}</p></>;
  const numbers = new Map(parseAnswer(typeof result.answer === "string" ? result.answer : "").citations.map(item => [item.spanId, item.n]));
  const table = selected ? matrixLines(rows, selected) : undefined;
  const counterNote = table ? noCounterNote(rows, table) : null;
  const shown = table?.filter(line => !onlyOpen || line.cells.some(cell => cell !== "SUPPORT"));
  return <>
    <GroundingRowsNotice result={result}/>
    {causeUnconfirmed(result) && <CauseUnconfirmed/>}
    <TestOrderSection rows={rows} actions={objectList(objectValue(result.action_plan).alternatives)} discrimination={discrimination}/>
    <LessonRecall links={links} lessons={lessons} shownIds={rows.map(row => row.id)}/>
    {rows.map((row, index) => <section className="detail-card hypothesis-card" key={row.id || index}>
      <HypothesisFields row={row}/>
      {standingLine(standingOf(discrimination, row.id)) && <p className="recorded-standing"><strong>{standingLine(standingOf(discrimination, row.id))}</strong><br/><small className="muted">{standingNote()}</small></p>}
      {projectId && <HypothesisLinkNotice projectId={projectId} link={links?.find(item => item.hypothesis_id === row.id)} all={links} distribution={distribution}/>}
      {canDraft && projectId && row.id && <SameHypothesis projectId={projectId} hypothesisId={row.id} same={same} candidates={links} discrimination={discrimination}/>}
      <p>{row.uncertainty}</p>
      <p className="counter-state">{row.counterTerminal ? formatMessage("counterSearch", counterReviewLabel(row.counterTerminal)) : t("hypotheses.counterNotRun")}</p>
      {row.counterTerminal && row.counter.length === 0 && <p className="muted">{t("hypotheses.noCounter")}</p>}
      <div className="hypothesis-counts">
        <Tag minimal intent="success">{t("hypotheses.supporting")}{" "}{row.support.length}</Tag>{" · "}<Tag minimal intent="danger">{t("hypotheses.refute")}{" "}{row.counter.length > 0 || row.counterTerminal ? row.counter.length : "-"}</Tag>
        {row.unevaluated !== null && <>{" · "}<Tag minimal>{t("hypotheses.uncomparedEvidence")}{" "}{row.unevaluated}/{selected?.length}</Tag></>}
      </div>
      <h4>{t("hypotheses.counterQuestions")}</h4>{row.counterQueries.map((query, j) => <p key={j}>{query}</p>)}
      {row.aiRefutation.length > 0 && <div className="ai-refutation"><h4>{t("hypotheses.refutationDraft")}</h4>
        <p className="muted">{t("hypotheses.refutationNotice")}</p>
        <ul>{row.aiRefutation.map((line, j) => <li key={j}>{line}</li>)}</ul></div>}
      <h4>{t("hypotheses.tests")}</h4>{row.tests.map((test, j) => <div key={j}><p>{String(test.procedure_candidate ?? "")}</p>
        <p>{t("labels.ifTrue")}{" "}{String(test.expected_if_true ?? "")}<br/>{t("labels.alternative")}{" "}{String(test.expected_if_alternative ?? "")}</p>
        <ExpectedResults test={test} rows={rows} onOpenEvidence={onOpenEvidence}/>
        {canDraft && projectId && row.id && typeof test.test_id === "string" && test.test_id && <>
          <TestResultRecorder projectId={projectId} hypothesisId={row.id} testId={test.test_id}
            recorded={discrimination?.find(item => item.hypothesis_id === row.id)?.results.find(item => item.test_id === test.test_id)}/>
          <TestActionDraft projectId={projectId} hypothesisId={row.id} testId={test.test_id} standing={standingOf(discrimination, row.id)}/></>}</div>)}
      {canDraft && projectId && row.id && <RefutationConditionsPanel projectId={projectId} hypothesisId={row.id} item={discrimination?.find(item => item.hypothesis_id === row.id)}/>}
      {review && row.id && <ReviewStatusLine controls={review} hypothesisId={row.id} statement={row.statement}/>}
      {row.id && <details className="connection-tech"><summary>{t("oauth.technical")}</summary><small>{row.id}</small></details>}
    </section>)}
    {table && shown && <section className="detail-card" aria-label={t("hypotheses.relationships")}>
      <h3>{t("hypotheses.relationships")}</h3>
      <p className="muted">{t("hypotheses.relationshipNotice")}</p>
      {counterNote && <p className="muted">{counterNote}</p>}
      <div className="relation-filter">
        <Button small minimal active={onlyOpen} onClick={() => setOnlyOpen(true)}>{t("hypotheses.filter")}</Button>
        <Button small minimal active={!onlyOpen} onClick={() => setOnlyOpen(false)}>{t("hypotheses.all")}</Button>
        <span className="relation-summary">{formatMessage("shownCount", shown.length, table.length)}</span>
      </div>
      <HTMLTable compact className="relation-table">
        <thead><tr><th>{t("history.evidence")}</th>{rows.map((row, index) => <th key={row.id || index} title={row.statement}>{t("hypotheses.hypothesis")}{" "}{index + 1}</th>)}</tr></thead>
        <tbody>{shown.map(line => <tr key={line.ref}>
          <td title={line.ref}>{numbers.has(line.ref) ? `[${numbers.get(line.ref)}]` : formatMessage("selectedEvidence", line.index + 1)}
            {line.splits && <> <Tag minimal intent="primary" title={t("hypotheses.discriminatingHint")}>{t("hypotheses.discriminating")}</Tag></>}</td>
          {line.cells.map((cell, index) => <td key={index}><Tag minimal intent={relationIntent[cell]}>{relationLabel[cell]}</Tag>
            {review && rows[index].id && !openRequest(review.requests, rows[index].id) && <Button small minimal icon="edit" className="cell-review"
              aria-label={formatMessage("reviewCell", index + 1, numbers.has(line.ref) ? formatMessage("evidenceRef", numbers.get(line.ref)) : formatMessage("selectedEvidence", line.index + 1))}
              onClick={() => review.onRequest({ hypothesisId: rows[index].id, statement: rows[index].statement, evidenceRef: line.ref,
                evidenceLabel: numbers.has(line.ref) ? formatMessage("evidenceRef", numbers.get(line.ref)) : formatMessage("selectedEvidence", line.index + 1) })}/>}</td>)}
        </tr>)}</tbody>
      </HTMLTable>
    </section>}
  </>;
}
