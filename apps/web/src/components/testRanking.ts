/**
 * The order of discriminating tests and action candidates for the hypotheses of one trace row. A pure rule, no score and no probability:
 * tests that leave the fewest cause candidates whichever way the result goes come first; ties go to the lower rule risk, then the lower
 * cost, then a test that can be undone, then the order they were made in. What cannot be ranked that way is set apart in named groups.
 * Nothing here reads a lesson, a count of past results or how plausible a hypothesis looks.
 */

export type Outcome = "WITH_TEST" | "WITH_ALTERNATIVE";
export type RankItem = {
  id: string; kind: "TEST" | "ACTION";
  /** The hypothesis a test is about (it expects its own result if that hypothesis is right); null for an action. */
  hypothesisId: string | null;
  /** The rule's risk tier; null when no effect was ever declared, which is counted as protected. R4 is left out of the list. */
  riskTier: string | null;
  /** The estimated cost when known; null is "unknown", never zero. */
  cost: number | null;
  reversibility: string | null;
  executable: boolean;
  resultRecorded: boolean;
  /** For every other candidate, which result it expects. Null (or missing a candidate) means the split cannot be counted. */
  predictions: Record<string, Outcome> | null;
  /**
   * The result each hypothesis expects from this test, as a short label (the tested hypothesis included); the same label is the same
   * result and "모름" is not known. When given it is used instead of `predictions`. A candidate with no entry means the split cannot be counted.
   */
  labels?: Record<string, string> | null;
};
export type RankHypothesis = { id: string; elimination: "SINGLE" | "REPEATED" | null };
export type RankInput = { hypotheses: RankHypothesis[]; actions: { id: string; hypothesisIds: string[] }[]; items: RankItem[] };

export type GroupId = "READY" | "COST_UNKNOWN" | "NO_DECISION_CHANGE" | "NOT_EXECUTABLE" | "UNCOUNTABLE" | "RESULT_RECORDED" | "HYPOTHESIS_ELIMINATED";
// A test that cannot be run now comes after the ones that can, even those whose split cannot be counted.
export const GROUP_ORDER: GroupId[] = ["READY", "COST_UNKNOWN", "NO_DECISION_CHANGE", "UNCOUNTABLE", "NOT_EXECUTABLE", "RESULT_RECORDED", "HYPOTHESIS_ELIMINATED"];
export type Ranked = {
  id: string; group: GroupId;
  /** The most cause candidates that can be left, whichever result comes; for a split that cannot be counted, candidates minus one (at least one). */
  worst: number;
  counted: boolean; tie: boolean; costUnknown: boolean;
  /** The test's hypothesis has one result that fits the other explanation: it stays in the count and is only marked. */
  eliminatedOnce: boolean;
};
export type RankOutput = { groups: { group: GroupId; items: Ranked[] }[]; excluded: string[]; candidates: number };

const RISK: Record<string, number> = { R0: 0, R1: 1, R2: 2, R3: 3 };
const riskRank = (tier: string | null) => (tier === null ? 3 : RISK[tier] ?? 3);
const reversibilityRank = (value: string | null) => (value === "FULL" ? 0 : value === "PARTIAL" ? 1 : 2);
const compareKeys = (a: number[], b: number[]) => {
  const at = a.findIndex((value, position) => value !== b[position]);
  return at === -1 ? 0 : a[at] - b[at];
};
const UNKNOWN_LABEL = "모름";
const normalized = (label: string) => label.trim().replace(/\s+/g, " ").toLowerCase();

/** The candidates left under each possible result of a test; null when the table does not cover every candidate. A hypothesis that does not know stays under every result. */
function outcomeClasses(item: RankItem, candidates: string[], tester: string | null): string[][] | null {
  if (item.kind !== "TEST" || tester === null || !candidates.includes(tester)) return null;
  const others = candidates.filter(id => id !== tester);
  if (item.labels) {
    const table = item.labels;
    if (!candidates.every(id => id in table)) return null;
    const unknown = candidates.filter(id => normalized(table[id]) === normalized(UNKNOWN_LABEL));
    const byLabel = new Map<string, string[]>();
    for (const id of candidates) if (!unknown.includes(id)) byLabel.set(normalized(table[id]), [...(byLabel.get(normalized(table[id])) ?? []), id]);
    return byLabel.size === 0 ? [unknown] : [...byLabel.values()].map(members => [...members, ...unknown]);
  }
  const table = item.predictions;
  if (table === null || !others.every(id => id in table)) return null;
  return [[tester, ...others.filter(id => table[id] === "WITH_TEST")], others.filter(id => table[id] === "WITH_ALTERNATIVE")];
}

/** Rank tests and actions; the same input always gives the same output. */
export function rankItems(input: RankInput): RankOutput {
  const eliminated = new Set(input.hypotheses.filter(item => item.elimination === "REPEATED").map(item => item.id));
  const once = new Set(input.hypotheses.filter(item => item.elimination === "SINGLE").map(item => item.id));
  const candidates = input.hypotheses.filter(item => !eliminated.has(item.id)).map(item => item.id);
  const actionsFor = (ids: string[]) => new Set(input.actions.filter(action => action.hypothesisIds.some(id => ids.includes(id))).map(action => action.id));
  const sameSet = (a: Set<string>, b: Set<string>) => a.size === b.size && [...a].every(id => b.has(id));
  const excluded: string[] = [];
  const placed: { ranked: Ranked; key: number[] }[] = [];
  input.items.forEach((item, index) => {
    if (item.riskTier === "R4") { excluded.push(item.id); return; }
    const tester = item.hypothesisId;
    const classes = outcomeClasses(item, candidates, tester);
    const counted = classes !== null;
    const worst = classes ? Math.max(...classes.map(members => members.length)) : Math.max(1, candidates.length - 1);
    const neutral = classes !== null && input.actions.length > 0 && classes.every(members => sameSet(actionsFor(members), actionsFor(classes[0])));
    const group: GroupId = tester !== null && eliminated.has(tester) ? "HYPOTHESIS_ELIMINATED"
      : item.resultRecorded ? "RESULT_RECORDED"
      : !item.executable ? "NOT_EXECUTABLE"
      : neutral ? "NO_DECISION_CHANGE"
      : !counted ? "UNCOUNTABLE"
      : item.cost === null ? "COST_UNKNOWN" : "READY";
    placed.push({ key: [worst, riskRank(item.riskTier), item.cost ?? Number.POSITIVE_INFINITY, reversibilityRank(item.reversibility), index],
      ranked: { id: item.id, group, worst, counted, tie: false, costUnknown: item.cost === null, eliminatedOnce: tester !== null && once.has(tester) } });
  });
  const groups = GROUP_ORDER.map(group => {
    const members = placed.filter(entry => entry.ranked.group === group).sort((a, b) => compareKeys(a.key, b.key));
    const same = (a: number[], b: number[]) => a.slice(0, 4).every((value, at) => value === b[at]);
    members.forEach((entry, at) => { entry.ranked.tie = (at > 0 && same(entry.key, members[at - 1].key)) || (at < members.length - 1 && same(entry.key, members[at + 1].key)); });
    return { group, items: members.map(entry => entry.ranked) };
  }).filter(entry => entry.items.length > 0);
  return { groups, excluded, candidates: candidates.length };
}
