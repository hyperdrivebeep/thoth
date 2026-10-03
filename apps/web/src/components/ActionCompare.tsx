import { HTMLTable, Tag } from "@blueprintjs/core";
import { objectList, objectValue, stringValues, textValue } from "../api/presentation";
import { numberSources } from "./answerText";
import { ActionEffort, EffortLines } from "./ActionEffort";
import { ApprovalSection } from "./ApprovalSection";
import { parseEstimates } from "./effortEstimates";
import { StatusBadge } from "./StatusBadge";
import { actionFamilyLabel, actionStateLabel, reversibilityLabel } from "./statusLabels";

const legacyEffort = (action: Record<string, unknown>) => action.estimated_cost == null && action.estimated_seconds == null ? undefined
  : `기록됨 (${String(action.estimated_cost ?? "비용 미확인")} · ${String(action.estimated_seconds ?? "시간 미확인")})`;

/** Next actions: the model's recommendation, the criteria it used, and how each action fares. No score, no rank. */
export function ActionCompare({ result, projectId }: { result: Record<string, unknown>; projectId?: string }) {
  const plan = objectValue(result.action_plan);
  const actions = objectList(plan.alternatives);
  const analysis = objectValue(plan.decision_analysis);
  const criteria = objectList(analysis.criteria);
  const evaluations = objectList(analysis.evaluations);
  const extra = [...actions.flatMap(action => stringValues(action.source_refs)), ...evaluations.flatMap(item => stringValues(item.evidence_refs))];
  const numbers = numberSources(typeof result.answer === "string" ? result.answer : "", extra);
  const cite = (refs: string[]) => refs.map(ref => <span className="source-number" key={ref} title={ref}>[{numbers.get(ref)}]</span>);
  if (actions.length === 0) return <p>아직 제안된 행동이 없습니다.</p>;
  const columnLabel = (action: Record<string, unknown>, index: number) => `행동 ${index + 1}${actionFamilyLabel(textValue(action.action_family)) ? ` · ${actionFamilyLabel(textValue(action.action_family))}` : ""}`;
  return <>
    {textValue(analysis.decision) && <section className="detail-card recommendation" aria-label="추천">
      <h3>추천</h3>
      <p>{textValue(analysis.decision)}</p>
      {textValue(analysis.uncertainty) && <p><strong>불확실성</strong><br/>{textValue(analysis.uncertainty)}</p>}
      {textValue(analysis.sensitivity) && <p><strong>이 판단이 바뀌는 조건</strong><br/>{textValue(analysis.sensitivity)}</p>}
      {textValue(analysis.preference_question) && <p className="result-notice"><strong>확인하고 싶은 점</strong><br/>{textValue(analysis.preference_question)}</p>}
    </section>}
    {projectId && textValue(plan.plan_id) && actions.some(action => action.execution_authority === "HUMAN_REQUIRED_R3")
      && <ApprovalSection projectId={projectId} planId={textValue(plan.plan_id)}/>}
    {criteria.length > 0 && <section className="detail-card" aria-label="필수 기준">
      <h3>필수 기준</h3>
      <ul>{criteria.map((criterion, index) => <li key={index}>{criterion.mandatory === true && <Tag minimal intent="warning">필수</Tag>} <strong>{textValue(criterion.name)}</strong>
        {textValue(criterion.rationale) && <><br/><small>{textValue(criterion.rationale)}</small></>}</li>)}</ul>
      {evaluations.length > 0 && <HTMLTable compact className="criteria-table">
        <thead><tr><th>기준</th>{actions.map((action, index) => <th key={index} title={textValue(action.specification)}>{columnLabel(action, index)}</th>)}</tr></thead>
        <tbody>{criteria.map((criterion, index) => <tr key={index}><th scope="row">{textValue(criterion.name)}</th>
          {actions.map((action, column) => <td key={column}>{evaluations.filter(item => item.action_id === action.action_id && item.criterion_id === criterion.criterion_id)
            .map((item, k) => <p key={k}>{textValue(item.scenario) && <em>{textValue(item.scenario)}: </em>}{textValue(item.assessment)} {cite(stringValues(item.evidence_refs))}</p>)}</td>)}</tr>)}</tbody>
      </HTMLTable>}
    </section>}
    {actions.map((action, index) => <section className="detail-card action-card" key={index}>
      <h3>{textValue(action.specification)}</h3>
      <div className="badge-row"><StatusBadge value={textValue(action.risk_tier)}/><StatusBadge value={textValue(action.execution_authority)}/>
        <Tag minimal>{reversibilityLabel(textValue(action.reversibility)) ?? "되돌림 미확인"}</Tag>
        {actionStateLabel(textValue(action.state)) && <Tag minimal>{actionStateLabel(textValue(action.state))}</Tag>}</div>
      <p>{textValue(action.expected_information_value)}</p>
      {projectId && textValue(action.action_id)
        ? <ActionEffort projectId={projectId} actionId={textValue(action.action_id)} initial={action.effort_estimates} legacy={legacyEffort(action)}/>
        : <EffortLines estimates={parseEstimates(action.effort_estimates)} legacy={legacyEffort(action)}/>}
      {stringValues(action.missing_evidence).map((value, j) => <p key={j}>필요한 근거: {value}</p>)}
      {stringValues(action.source_refs).length > 0 && <p>근거 {cite(stringValues(action.source_refs))}</p>}
      <p>{action.execution_authority === "HUMAN_REQUIRED_R3" ? "보호된 실행 제안 · 대상과 영향에 대한 권한 결정이 필요합니다." : "제안과 실제 실행 결과는 별도로 기록됩니다."}</p>
    </section>)}
  </>;
}
