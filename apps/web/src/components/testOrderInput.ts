import type { DiscriminationItem, TestResult } from "../api/judgmentRecords";
import { objectList, stringValues, textValue } from "../api/presentation";
import type { HypothesisRow } from "./hypothesisView";
import type { RankInput, RankItem } from "./testRanking";
import { EXCLUDED_TITLE, groupTitle } from "./testOrderText";
import { rankItems } from "./testRanking";

export type TestLabel = { statement: string; procedure: string; cost: number | null; recorded: TestResult | undefined };

const costOf = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

/**
 * The tests of a row's hypotheses, as the ranking takes them. The risk is the rule's, which a test does not have until an action request
 * is drafted from it, so it is left undeclared (counted as protected); the model's own tier is never read. Nothing here knows what the
 * other hypotheses expect from a test, so the split is not countable until a table says so.
 */
export function testOrderInput(rows: HypothesisRow[], actions: Record<string, unknown>[], discrimination: DiscriminationItem[] | undefined): { input: RankInput; labels: Map<string, TestLabel> } {
  const labels = new Map<string, TestLabel>();
  const items: RankItem[] = [];
  for (const row of rows) {
    const known = discrimination?.find(item => item.hypothesis_id === row.id);
    row.tests.forEach((test, at) => {
      const id = row.id + "::" + (textValue(test.test_id) || "#" + at);
      const recorded = known?.results.find(item => item.test_id === textValue(test.test_id));
      labels.set(id, { statement: row.statement, procedure: textValue(test.procedure_candidate), cost: costOf(test.estimated_cost), recorded });
      items.push({ id, kind: "TEST", hypothesisId: row.id || null, riskTier: null, cost: costOf(test.estimated_cost), reversibility: textValue(test.reversibility) || null,
        executable: test.executable !== false, resultRecorded: recorded !== undefined, predictions: null, labels: labelsOf(test) });
    });
  }
  return { labels, input: {
    hypotheses: rows.filter(row => row.id).map(row => ({ id: row.id, elimination: discrimination?.find(item => item.hypothesis_id === row.id)?.elimination ?? null })),
    actions: actions.map((action, at) => ({ id: textValue(action.action_id) || "#" + at, hypothesisIds: [...stringValues(action.hypothesis_ids), ...stringValues(action.hypothesis_refs)] })),
    items } };
}

/** The action candidates of a result, as the ranking takes them: the rule's risk, the estimate when there is one, no split to count. */
export function actionOrderItems(actions: Record<string, unknown>[]): RankItem[] {
  return actions.map((action, at) => ({ id: textValue(action.action_id) || "#" + at, kind: "ACTION", hypothesisId: null, riskTier: textValue(action.risk_tier) || null, cost: costOf(action.estimated_cost),
    reversibility: textValue(action.reversibility) || null, executable: true, resultRecorded: false, predictions: null }));
}

/** The action cards in the order of the rule, each with the heading to print above it (none when there is only one kind of group). */
export function orderedActions(actions: Record<string, unknown>[]): { action: Record<string, unknown>; heading: string | null; key: string }[] {
  const items = actionOrderItems(actions);
  const byId = new Map(items.map((item, at) => [item.id, actions[at]]));
  const ranked = rankItems({ hypotheses: [], actions: [], items });
  const sections = ranked.groups.length + (ranked.excluded.length > 0 ? 1 : 0);
  const out: { action: Record<string, unknown>; heading: string | null; key: string }[] = [];
  for (const group of ranked.groups) group.items.forEach((entry, at) => out.push({ action: byId.get(entry.id)!, key: entry.id, heading: sections > 1 && at === 0 ? groupTitle(group.group, "ACTION") : null }));
  ranked.excluded.forEach((id, at) => out.push({ action: byId.get(id)!, key: id, heading: at === 0 ? EXCLUDED_TITLE : null }));
  return out;
}
/** The result each hypothesis is said to expect from a test (contract v3), by hypothesis id; null when the result carries no table. */
function labelsOf(test: Record<string, unknown>): Record<string, string> | null {
  const table: Record<string, string> = {};
  for (const row of objectList(test.expected_by_hypothesis)) {
    const id = textValue(row.hypothesis_id), expected = textValue(row.expected);
    if (id && expected && !(id in table)) table[id] = expected;
  }
  return Object.keys(table).length > 0 ? table : null;
}
