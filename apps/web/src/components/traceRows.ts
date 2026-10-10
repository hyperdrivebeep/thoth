import type { TraceItem, TraceResult, TraceRule, TraceView, Verdict } from "../api/trace";

export type CriterionRow = { item: TraceItem; rule: TraceRule | undefined; verdict: Verdict | undefined; chosen: TraceResult[] };
export type RequirementGroup = { item: TraceItem | null; verdict: Verdict | undefined; criteria: CriterionRow[] };

const byId = (a: TraceItem, b: TraceItem) => a.item_id.localeCompare(b.item_id);

/** One group per requirement (its criteria beneath it); criteria linked to no requirement come last. */
export function buildGroups(view: TraceView): RequirementGroup[] {
  const verdict = (kind: string, id: string) => view.verdicts.find(item => item.subject_kind === kind && item.subject_id === id);
  const criteria = new Map(view.items.filter(item => item.kind === "CRITERION").map(item => [item.item_id, item]));
  const row = (item: TraceItem): CriterionRow => {
    const found = verdict("CRITERION", item.item_id);
    const chosen = (found?.selection?.chosen ?? []).flatMap(ref => view.results.filter(result => result.result_id === ref.result_id && result.result_revision === ref.result_revision));
    return { item, rule: view.rules.find(rule => rule.criterion_id === item.item_id), verdict: found, chosen };
  };
  const linked = new Set<string>();
  const groups = view.items.filter(item => item.kind === "REQUIREMENT").sort(byId).map(requirement => {
    const ids = view.links.filter(link => link.relation === "REFINES" && link.to_id === requirement.item_id).map(link => link.from_id);
    const members = ids.flatMap(id => (criteria.has(id) ? [criteria.get(id)!] : [])).sort(byId);
    members.forEach(item => linked.add(item.item_id));
    return { item: requirement, verdict: verdict("REQUIREMENT", requirement.item_id), criteria: members.map(row) };
  });
  const loose = [...criteria.values()].filter(item => !linked.has(item.item_id)).sort(byId);
  return loose.length > 0 ? [...groups, { item: null, verdict: undefined, criteria: loose.map(row) }] : groups;
}

export const titleOf = (view: TraceView) => (id: string) => view.items.find(item => item.item_id === id)?.title || id;

export function ruleText(rule: TraceRule | undefined) {
  if (!rule) return "판정 규칙 없음";
  const unit = rule.unit ? ` (${rule.unit})` : "";
  return `${rule.threshold} ${rule.comparator === ">=" ? "이상" : "이하"}${unit}${rule.required ? "" : " · 필수 아님"}`;
}

export function resultText(result: TraceResult) {
  const counts = result.numerator !== null && result.denominator !== null ? ` (${result.numerator}/${result.denominator})` : "";
  const value = result.value ?? (result.raw_value ? `${result.raw_value}(숫자 아님)` : "값 없음");
  return `${value}${result.unit ? ` ${result.unit}` : ""}${counts}`;
}

const MARK = "SYNTHETIC DEMO DATA";
/** Data written for a demo says so on the screen; the table itself cannot tell a demo from real values. */
export const isSyntheticTrace = (view: TraceView) =>
  view.items.some(item => item.title.includes(MARK)
    || Object.entries(item.fields).some(([key, value]) => key === "synthetic_notice" || value.includes(MARK)));
