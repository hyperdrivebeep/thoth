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
import { STANDING_NOTE, standingLine } from "./judgmentRecordText";
import type { SameView } from "../api/hypothesisSame";
import { SameHypothesis } from "./SameHypothesis";

const relationLabel: Record<Relation, string> = { SUPPORT: "지지", COUNTER: "반박", NOT_APPLICABLE: "해당 없음", UNEVALUATED: "미평가" };
const relationIntent = { SUPPORT: "success", COUNTER: "danger", NOT_APPLICABLE: "none", UNEVALUATED: "none" } as const;
const standingOf = (items: DiscriminationItem[] | undefined, id: string) => items?.find(item => item.hypothesis_id === id)?.standing;

/** Hypothesis cards and, when the answer's selected sources are known, a source-by-hypothesis relation table. */
export function HypothesisCompare({ result, review, projectId, links, distribution, canDraft, discrimination, lessons, same }: {
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
}) {
  const rows = hypothesisRows(result);
  const selected = selectedEvidence(result);
  const [onlyOpen, setOnlyOpen] = useState(true);
  if (rows.length === 0) return <p>이 답변에는 제안된 가설이 없습니다.</p>;
  const numbers = new Map(parseAnswer(typeof result.answer === "string" ? result.answer : "").citations.map(item => [item.spanId, item.n]));
  const table = selected ? matrixLines(rows, selected) : undefined;
  const counterNote = table ? noCounterNote(rows, table) : null;
  const shown = table?.filter(line => !onlyOpen || line.cells.some(cell => cell !== "SUPPORT"));
  return <>
    {causeUnconfirmed(result) && <CauseUnconfirmed/>}
    <TestOrderSection rows={rows} actions={objectList(objectValue(result.action_plan).alternatives)} discrimination={discrimination}/>
    <LessonRecall links={links} lessons={lessons} shownIds={rows.map(row => row.id)}/>
    {rows.map((row, index) => <section className="detail-card hypothesis-card" key={row.id || index}>
      <HypothesisFields row={row}/>
      {standingLine(standingOf(discrimination, row.id)) && <p className="recorded-standing"><strong>{standingLine(standingOf(discrimination, row.id))}</strong><br/><small className="muted">{STANDING_NOTE}</small></p>}
      {projectId && <HypothesisLinkNotice projectId={projectId} link={links?.find(item => item.hypothesis_id === row.id)} all={links} distribution={distribution}/>}
      {canDraft && projectId && row.id && <SameHypothesis projectId={projectId} hypothesisId={row.id} same={same} candidates={links} discrimination={discrimination}/>}
      <p>{row.uncertainty}</p>
      <p className="counter-state">{row.counterTerminal ? `반대 근거 탐색: ${counterReviewLabel(row.counterTerminal)}` : "반대 근거 탐색 안 함"}</p>
      {row.counterTerminal && row.counter.length === 0 && <p className="muted">반박 근거를 찾지 못함 (지지된다는 뜻은 아닙니다)</p>}
      <div className="hypothesis-counts">
        <Tag minimal intent="success">뒷받침 {row.support.length}</Tag>{" · "}<Tag minimal intent="danger">반박 {row.counterTerminal ? row.counter.length : "-"}</Tag>
        {row.unevaluated !== null && <>{" · "}<Tag minimal>미평가 {row.unevaluated}</Tag></>}
      </div>
      <h4>반대 근거를 찾을 질문</h4>{row.counterQueries.map((query, j) => <p key={j}>{query}</p>)}
      {row.aiRefutation.length > 0 && <div className="ai-refutation"><h4>AI 제안(미확정) 기각 조건</h4>
        <p className="muted">모델이 낸 제안입니다. 사람이 확인하기 전에는 기각 조건으로 보지 않으며, 사람이 적은 기각 조건과 따로 둡니다.</p>
        <ul>{row.aiRefutation.map((line, j) => <li key={j}>{line}</li>)}</ul></div>}
      <h4>구별할 시험</h4>{row.tests.map((test, j) => <div key={j}><p>{String(test.procedure_candidate ?? "")}</p>
        <p>참일 때: {String(test.expected_if_true ?? "")}<br/>다른 설명일 때(이 가설을 버릴 신호): {String(test.expected_if_alternative ?? "")}</p>
        <ExpectedResults test={test} rows={rows}/>
        {canDraft && projectId && row.id && typeof test.test_id === "string" && test.test_id && <>
          <TestResultRecorder projectId={projectId} hypothesisId={row.id} testId={test.test_id}
            recorded={discrimination?.find(item => item.hypothesis_id === row.id)?.results.find(item => item.test_id === test.test_id)}/>
          <TestActionDraft projectId={projectId} hypothesisId={row.id} testId={test.test_id} standing={standingOf(discrimination, row.id)}/></>}</div>)}
      {canDraft && projectId && row.id && <RefutationConditionsPanel projectId={projectId} hypothesisId={row.id} item={discrimination?.find(item => item.hypothesis_id === row.id)}/>}
      {review && row.id && <ReviewStatusLine controls={review} hypothesisId={row.id} statement={row.statement}/>}
      {row.id && <details className="connection-tech"><summary>기술 정보</summary><small>{row.id}</small></details>}
    </section>)}
    {table && shown && <section className="detail-card" aria-label="근거와 가설의 관계">
      <h3>근거와 가설의 관계</h3>
      <p className="muted">AI가 판단한 관계입니다. 칸을 직접 고치지 않고, 자료 추가나 방향 지시로 다시 조사하게 합니다. 아직 살펴보지 않은 칸은 "미평가"로 보입니다.</p>
      {counterNote && <p className="muted">{counterNote}</p>}
      <div className="relation-filter">
        <Button small minimal active={onlyOpen} onClick={() => setOnlyOpen(true)}>반박·미평가만</Button>
        <Button small minimal active={!onlyOpen} onClick={() => setOnlyOpen(false)}>전체 보기</Button>
        <span className="relation-summary">{`표시 ${shown.length}/전체 ${table.length}`}</span>
      </div>
      <HTMLTable compact className="relation-table">
        <thead><tr><th>근거</th>{rows.map((row, index) => <th key={row.id || index} title={row.statement}>가설 {index + 1}</th>)}</tr></thead>
        <tbody>{shown.map(line => <tr key={line.ref}>
          <td title={line.ref}>{numbers.has(line.ref) ? `[${numbers.get(line.ref)}]` : `선택 근거 ${line.index + 1}`}
            {line.splits && <> <Tag minimal intent="primary" title="이 근거는 가설마다 관계가 달라 어느 설명이 맞는지 가리는 데 쓸 수 있습니다.">가설을 가름</Tag></>}</td>
          {line.cells.map((cell, index) => <td key={index}><Tag minimal intent={relationIntent[cell]}>{relationLabel[cell]}</Tag>
            {review && rows[index].id && !openRequest(review.requests, rows[index].id) && <Button small minimal icon="edit" className="cell-review"
              aria-label={`재검토 요청 · 가설 ${index + 1} · ${numbers.has(line.ref) ? `근거 [${numbers.get(line.ref)}]` : `선택 근거 ${line.index + 1}`}`}
              onClick={() => review.onRequest({ hypothesisId: rows[index].id, statement: rows[index].statement, evidenceRef: line.ref,
                evidenceLabel: numbers.has(line.ref) ? `근거 [${numbers.get(line.ref)}]` : `선택 근거 ${line.index + 1}` })}/>}</td>)}
        </tr>)}</tbody>
      </HTMLTable>
    </section>}
  </>;
}
