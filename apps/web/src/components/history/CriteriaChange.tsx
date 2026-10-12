import { message as formatMessage } from "../../i18n/formatMessages";
import { t } from "../../i18n/locale";
import { Tag } from "@blueprintjs/core";
import type { CriterionDelta, DecisionDelta } from "../../api/researchFollowup";
import { coverageTermLabel, statusLabel } from "../statusLabels";

type State = NonNullable<CriterionDelta["before"]>;

const term = (value: string) => (value && value !== "NOT_ASSESSED" ? coverageTermLabel(value) : t("criteriaChange.none"));
const status = (state: State | null) => (state ? statusLabel(state.status) ?? state.status : t("criteriaChange.none"));
const judgment = (state: State | null) => (state ? [state.relation, state.validation].map(term).join(" · ") : t("criteriaChange.none"));

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
    {item.match === "ADDED" && <Tag minimal intent="primary">{t("criteriaChange.added")}</Tag>}
    {item.match === "REMOVED" && <Tag minimal>{t("criteriaChange.removed")}</Tag>}
    <div><small>{item.match === "SAME" ? `${status(before)} → ${status(after)}` : formatMessage("criterionStatus", status(after ?? before))}</small></div>
    {item.match === "SAME" && before && after && (before.relation !== after.relation || before.validation !== after.validation) &&
      <div><small>{formatMessage("judgmentChange", judgment(before), judgment(after))}</small></div>}
    <div><small>{item.match === "SAME" && before && after && before.blocker !== after.blocker
      ? formatMessage("holdChange", term(before.blocker), term(after.blocker))
      : formatMessage("holdReason", term((after ?? before)?.blocker ?? ""))}</small></div>
    {item.evidence_refs_added.length > 0 && <div><small>{formatMessage("addedEvidence", item.evidence_refs_added.length)}</small></div>}
    {item.evidence_refs_removed.length > 0 && <div><small>{formatMessage("removedEvidence", item.evidence_refs_removed.length)}</small></div>}
    {refs.length > 0 && <details className="connection-tech"><summary>{t("oauth.technical")}</summary><small>{refs.join(" · ")}</small></details>}
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
    return <section className="criteria-change" aria-label={t("criteriaChange.title")}><h4>{t("criteriaChange.title")}</h4>
      <p className="muted">{t("criteriaChange.unavailable")}</p></section>;
  }
  const moved = delta.criteria.filter(item => item.match !== "SAME");
  const same = delta.criteria.filter(item => item.match === "SAME");
  const holds = same.filter(item => item.unchanged_hold);
  const shifted = same.filter(item => !item.unchanged_hold && changed(item));
  const steady = same.filter(item => !item.unchanged_hold && !changed(item) && item.after?.status === "SATISFIED");
  return <section className="criteria-change" aria-label={t("criteriaChange.title")}><h4>{t("criteriaChange.title")}</h4>
    <Group title={t("criteriaChange.changed")} items={shifted}/>
    <Group title={t("criteriaChange.held")} items={holds}/>
    <Group title={t("criteriaChange.membership")} items={moved}/>
    <p className="muted">{t("criteriaChange.satisfied")}{" "}{steady.length}</p>
  </section>;
}

