import { Tag } from "@blueprintjs/core";
import type { DiscriminationItem } from "../api/judgmentRecords";
import type { HypothesisRow } from "./hypothesisView";
import { eliminationLine, matchLabel } from "./judgmentRecordText";
import { testOrderInput } from "./testOrderInput";
import { excludedTestsLine, groupHint, groupTitle, ORDER_NOTE, reductionLine } from "./testOrderText";
import { rankItems } from "./testRanking";

/** The next tests to try, in the order the rule gives, in named groups. No score, no probability; nothing here reads a lesson. */
export function TestOrderSection({ rows, actions, discrimination }: { rows: HypothesisRow[]; actions: Record<string, unknown>[]; discrimination?: DiscriminationItem[] }) {
  const { input, labels } = testOrderInput(rows, actions, discrimination);
  if (input.items.length === 0) return null;
  const ranked = rankItems(input);
  return <section className="detail-card test-order" aria-label="시험 순서">
    <h3>다음에 해 볼 시험의 순서</h3>
    <p className="muted">{ORDER_NOTE}</p>
    {ranked.excluded.length > 0 && <p className="muted">{excludedTestsLine(ranked.excluded.length)}</p>}
    {ranked.groups.map(group => <div key={group.group}>
      <h4>{groupTitle(group.group, "TEST")}</h4>
      <p className="muted">{groupHint(group.group)}</p>
      <ol>{group.items.map(entry => {
        const label = labels.get(entry.id)!;
        return <li key={entry.id}>
          <strong>{label.procedure || "시험 내용이 기록되지 않았습니다"}</strong>
          <br/><small className="muted">가설: {label.statement}</small>
          <br/><small>{reductionLine(entry, ranked.candidates)}</small>
          <div>{entry.costUnknown && <Tag minimal>비용 미상</Tag>}{label.cost !== null && <Tag minimal>비용 추정 {label.cost}</Tag>}
            {entry.tie && <Tag minimal title="순서를 가를 차이가 없어 만든 순서로 놓았습니다.">동점 · 만든 순서</Tag>}
            {entry.eliminatedOnce && <Tag minimal intent="warning" title="결과 한 건으로 확정하지 않습니다. 후보에는 그대로 셉니다.">{eliminationLine("SINGLE")}</Tag>}
            {label.recorded && <Tag minimal>기록된 결과: {matchLabel(label.recorded.matched)}</Tag>}</div>
        </li>;
      })}</ol>
    </div>)}
  </section>;
}
