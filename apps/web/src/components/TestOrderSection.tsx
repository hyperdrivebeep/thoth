import { t } from "../i18n/locale";
import { Tag } from "@blueprintjs/core";
import type { DiscriminationItem } from "../api/judgmentRecords";
import type { HypothesisRow } from "./hypothesisView";
import { eliminationLine, matchLabel } from "./judgmentRecordText";
import { testOrderInput } from "./testOrderInput";
import { excludedTestsLine, groupHint, groupTitle, orderNote, reductionLine } from "./testOrderText";
import { rankItems } from "./testRanking";

/** The next tests to try, in the order the rule gives, in named groups. No score, no probability; nothing here reads a lesson. */
export function TestOrderSection({ rows, actions, discrimination }: { rows: HypothesisRow[]; actions: Record<string, unknown>[]; discrimination?: DiscriminationItem[] }) {
  const { input, labels } = testOrderInput(rows, actions, discrimination);
  if (input.items.length === 0) return null;
  const ranked = rankItems(input);
  return <section className="detail-card test-order" aria-label={t("testOrder.title")}>
    <h3>{t("testOrder.nextTests")}</h3>
    <p className="muted">{orderNote()}</p>
    {ranked.excluded.length > 0 && <p className="muted">{excludedTestsLine(ranked.excluded.length)}</p>}
    {ranked.groups.map(group => <div key={group.group}>
      <h4>{groupTitle(group.group, "TEST")}</h4>
      <p className="muted">{groupHint(group.group)}</p>
      <ol>{group.items.map(entry => {
        const label = labels.get(entry.id)!;
        return <li key={entry.id}>
          <strong>{label.procedure || t("testOrder.noProcedure")}</strong>
          <br/><small className="muted">{t("labels.hypothesis")}{" "}{label.statement}</small>
          <br/><small>{reductionLine(entry, ranked.candidates)}</small>
          <div>{entry.costUnknown && <Tag minimal>{t("testOrder.costUnknown")}</Tag>}{label.cost !== null && <Tag minimal>{t("testOrder.cost")}{" "}{label.cost}</Tag>}
            {entry.tie && <Tag minimal title={t("testOrder.tieNotice")}>{t("testOrder.tie")}</Tag>}
            {entry.eliminatedOnce && <Tag minimal intent="warning" title={t("testOrder.singleNotice")}>{eliminationLine("SINGLE")}</Tag>}
            {label.recorded && <Tag minimal>{t("labels.recordedResult")}{" "}{matchLabel(label.recorded.matched)}</Tag>}</div>
        </li>;
      })}</ol>
    </div>)}
  </section>;
}
