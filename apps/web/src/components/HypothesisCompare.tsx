import { Button, HTMLTable, Tag } from "@blueprintjs/core";
import { useState } from "react";
import { hypothesisRows, relationOf, selectedEvidence, type Relation } from "./hypothesisView";
import { counterReviewLabel } from "./statusLabels";
import { parseAnswer } from "./answerText";
import { ReviewStatusLine } from "./ReviewStatusLine";
import { openRequest, type ReviewControls } from "./judgmentReview";

const relationLabel: Record<Relation, string> = { SUPPORT: "지지", COUNTER: "반박", UNEVALUATED: "미평가" };
const relationIntent = { SUPPORT: "success", COUNTER: "danger", UNEVALUATED: "none" } as const;

/** Hypothesis cards and, when the answer's selected sources are known, a source-by-hypothesis relation table. */
export function HypothesisCompare({ result, review }: { result: Record<string, unknown>; review?: ReviewControls }) {
  const rows = hypothesisRows(result);
  const selected = selectedEvidence(result);
  const [onlyOpen, setOnlyOpen] = useState(true);
  if (rows.length === 0) return <p>이 답변에는 제안된 가설이 없습니다.</p>;
  const numbers = new Map(parseAnswer(typeof result.answer === "string" ? result.answer : "").citations.map(item => [item.spanId, item.n]));
  const table = selected?.map((ref, index) => ({ ref, index, cells: rows.map(row => relationOf(row, ref)) }));
  const shown = table?.filter(line => !onlyOpen || line.cells.some(cell => cell !== "SUPPORT"));
  return <>
    {rows.map((row, index) => <section className="detail-card hypothesis-card" key={row.id || index}>
      <h3>{row.statement}</h3>
      <p>{row.uncertainty}</p>
      <p className="counter-state">{row.counterTerminal ? `반대 근거 탐색: ${counterReviewLabel(row.counterTerminal)}` : "반대 근거 탐색 안 함"}</p>
      <div className="hypothesis-counts">
        <Tag minimal intent="success">뒷받침 {row.support.length}</Tag>{" · "}<Tag minimal intent="danger">반박 {row.counterTerminal ? row.counter.length : "-"}</Tag>
        {row.unevaluated !== null && <>{" · "}<Tag minimal>미평가 {row.unevaluated}</Tag></>}
      </div>
      <h4>반대 근거를 찾을 질문</h4>{row.counterQueries.map((query, j) => <p key={j}>{query}</p>)}
      <h4>구별할 시험</h4>{row.tests.map((test, j) => <div key={j}><p>{String(test.procedure_candidate ?? "")}</p>
        <p>참일 때: {String(test.expected_if_true ?? "")}<br/>다른 설명일 때: {String(test.expected_if_alternative ?? "")}</p></div>)}
      {review && row.id && <ReviewStatusLine controls={review} hypothesisId={row.id} statement={row.statement}/>}
      {row.id && <details className="connection-tech"><summary>기술 정보</summary><small>{row.id}</small></details>}
    </section>)}
    {table && shown && <section className="detail-card" aria-label="근거와 가설의 관계">
      <h3>근거와 가설의 관계</h3>
      <p className="muted">AI가 판단한 관계입니다. 칸을 직접 고치지 않고, 자료 추가나 방향 지시로 다시 조사하게 합니다.</p>
      <div className="relation-filter">
        <Button small minimal active={onlyOpen} onClick={() => setOnlyOpen(true)}>반박·미평가만</Button>
        <Button small minimal active={!onlyOpen} onClick={() => setOnlyOpen(false)}>전체 보기</Button>
        <span className="relation-summary">{`표시 ${shown.length}/전체 ${table.length}`}</span>
      </div>
      <HTMLTable compact className="relation-table">
        <thead><tr><th>근거</th>{rows.map((row, index) => <th key={row.id || index} title={row.statement}>가설 {index + 1}</th>)}</tr></thead>
        <tbody>{shown.map(line => <tr key={line.ref}>
          <td title={line.ref}>{numbers.has(line.ref) ? `[${numbers.get(line.ref)}]` : `선택 근거 ${line.index + 1}`}</td>
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
