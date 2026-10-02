import { Tag } from "@blueprintjs/core";
import type { CriterionDelta, DecisionDelta } from "../../api/researchFollowup";
import { coverageTermLabel, statusLabel } from "../statusLabels";

type State = NonNullable<CriterionDelta["before"]>;

const term = (value: string) => (value && value !== "NOT_ASSESSED" ? coverageTermLabel(value) : "없음");
const status = (state: State | null) => (state ? statusLabel(state.status) ?? state.status : "없음");
const judgment = (state: State | null) => (state ? [state.relation, state.validation].map(term).join(" · ") : "없음");

function changed(item: CriterionDelta): boolean {
  const { before, after } = item;
  if (!before || !after) return true;
  return before.status !== after.status || before.relation !== after.relation || before.validation !== after.validation ||
    before.blocker !== after.blocker || item.evidence_refs_added.length > 0 || item.evidence_refs_removed.length > 0;
}

function Line({ item }: { item: CriterionDelta }) {
  const { before, after } = item;
  const refs = [...item.evidence_refs_added, ...item.evidence_refs_removed];
  return <li>
    <strong>{item.question}</strong>
    {item.match === "ADDED" && <Tag minimal intent="primary">새로 생김</Tag>}
    {item.match === "REMOVED" && <Tag minimal>없어짐</Tag>}
    <div><small>{item.match === "SAME" ? `${status(before)} → ${status(after)}` : `상태: ${status(after ?? before)}`}</small></div>
    {item.match === "SAME" && before && after && (before.relation !== after.relation || before.validation !== after.validation) &&
      <div><small>{`판단: ${judgment(before)} → ${judgment(after)}`}</small></div>}
    <div><small>{item.match === "SAME" && before && after && before.blocker !== after.blocker
      ? `보류 사유: ${term(before.blocker)} → ${term(after.blocker)}`
      : `보류 사유: ${term((after ?? before)?.blocker ?? "")}`}</small></div>
    {item.evidence_refs_added.length > 0 && <div><small>{`추가된 근거 ${item.evidence_refs_added.length}개`}</small></div>}
    {item.evidence_refs_removed.length > 0 && <div><small>{`빠진 근거 ${item.evidence_refs_removed.length}개`}</small></div>}
    {refs.length > 0 && <details className="connection-tech"><summary>기술 정보</summary><small>{refs.join(" · ")}</small></details>}
  </li>;
}

function Group({ title, items }: { title: string; items: CriterionDelta[] }) {
  return <section className="criteria-change-group">
    <h5>{title} {items.length}</h5>
    {items.length > 0 && <ul>{items.map((item, index) => <Line key={`${item.requirement_id}-${item.match}-${index}`} item={item}/>)}</ul>}
  </section>;
}

/**
 * Criterion-by-criterion view of two results: what changed, what is still on hold, and what appeared or went away.
 * The server pairs criteria only when id, kind, target and question all match, so a renumbered check is never shown as changed.
 */
export function CriteriaChange({ delta }: { delta: DecisionDelta }) {
  if (delta.criteria_state !== "AVAILABLE") {
    return <section className="criteria-change" aria-label="기준별 판단 변화"><h4>기준별 판단 변화</h4>
      <p className="muted">기준별 변화를 읽지 못했습니다. 두 답변의 평가기준 기록을 모두 읽을 수 있어야 비교합니다.</p></section>;
  }
  const moved = delta.criteria.filter(item => item.match !== "SAME");
  const same = delta.criteria.filter(item => item.match === "SAME");
  const holds = same.filter(item => item.unchanged_hold);
  const shifted = same.filter(item => !item.unchanged_hold && changed(item));
  const steady = same.filter(item => !item.unchanged_hold && !changed(item) && item.after?.status === "SATISFIED");
  return <section className="criteria-change" aria-label="기준별 판단 변화"><h4>기준별 판단 변화</h4>
    <Group title="바뀐 기준" items={shifted}/>
    <Group title="계속 보류" items={holds}/>
    <Group title="새로 생긴·없어진 기준" items={moved}/>
    <p className="muted">그대로 충족 {steady.length}</p>
  </section>;
}

