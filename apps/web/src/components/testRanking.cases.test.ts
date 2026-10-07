import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { GROUP_ORDER, rankItems, type GroupId, type RankInput } from "./testRanking";

type Case = { id: string; about: string; hypotheses: RankInput["hypotheses"]; actions: RankInput["actions"]; items: RankInput["items"];
  expected: { candidates: number; groups: { group: GroupId; ids: string[] }[]; worst: Record<string, number>; tie: string[]; excluded: string[]; eliminatedOnce?: string[] } };
const document = JSON.parse(readFileSync(new URL("../../../../tests/fixtures/hypothesis_eval/ranking_cases.json", import.meta.url), "utf8")) as { schema_version: string; notice: string; cases: Case[] };

const REQUIRED = ["order-balanced-split", "order-confirm-only", "order-cost-unknown", "order-tie", "order-infeasible", "order-no-decision-change", "noisy-single-result",
  "order-multi-cause", "result-recorded", "repeated-elimination"];
const outcome = (item: Case) => rankItems({ hypotheses: item.hypotheses, actions: item.actions, items: item.items });

describe("the ranking cases", () => {
  it("are synthetic, name the agreed situations, and each says what it is about", () => {
    expect(document.schema_version).toBe("hypothesis-ranking-cases/1");
    expect(document.notice).toContain("SYNTHETIC");
    const ids = document.cases.map(item => item.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of REQUIRED) expect(ids).toContain(id);
    for (const item of document.cases) expect(item.about.length).toBeGreaterThan(20);
  });

  for (const item of document.cases) {
    it("gives the expected order for " + item.id, () => {
      const result = outcome(item);
      expect(result.candidates).toBe(item.expected.candidates);
      expect(result.groups.map(group => ({ group: group.group, ids: group.items.map(entry => entry.id) }))).toEqual(item.expected.groups);
      expect(result.excluded).toEqual(item.expected.excluded);
      const all = result.groups.flatMap(group => group.items);
      for (const [id, worst] of Object.entries(item.expected.worst)) expect(all.find(entry => entry.id === id)?.worst).toBe(worst);
      expect(all.filter(entry => entry.tie).map(entry => entry.id).sort()).toEqual([...item.expected.tie].sort());
      expect(all.filter(entry => entry.eliminatedOnce).map(entry => entry.id)).toEqual(item.expected.eliminatedOnce ?? []);
      expect(outcome(item)).toEqual(result); // the same input gives the same order
    });
  }

  it("never puts an unknown cost before a known one of the same split and risk, a blocked or recorded test in the main list, or a forbidden test anywhere", () => {
    for (const item of document.cases) {
      const result = outcome(item);
      const byId = new Map(item.items.map(entry => [entry.id, entry]));
      for (const group of result.groups) {
        group.items.slice(1).forEach((entry, at) => {
          const before = group.items[at];
          const [a, b] = [byId.get(before.id)!, byId.get(entry.id)!];
          if (before.worst === entry.worst && (a.riskTier ?? "R3") === (b.riskTier ?? "R3")) expect(a.cost === null && b.cost !== null).toBe(false);
        });
        if (group.group === "READY") for (const entry of group.items) {
          const source = byId.get(entry.id)!;
          expect(source.executable && !source.resultRecorded && source.riskTier !== "R4" && source.cost !== null).toBe(true);
        }
      }
      const placed = new Set(result.groups.flatMap(group => group.items.map(entry => entry.id)));
      for (const id of result.excluded) { expect(placed.has(id)).toBe(false); expect(byId.get(id)!.riskTier).toBe("R4"); }
      expect(placed.size + result.excluded.length).toBe(item.items.length);
    }
  });

  it("lists the groups in the agreed order", () => {
    expect(GROUP_ORDER).toEqual(["READY", "COST_UNKNOWN", "NO_DECISION_CHANGE", "UNCOUNTABLE", "NOT_EXECUTABLE", "RESULT_RECORDED", "HYPOTHESIS_ELIMINATED"]);
    for (const item of document.cases) {
      const order = outcome(item).groups.map(group => GROUP_ORDER.indexOf(group.group));
      expect([...order].sort((a, b) => a - b)).toEqual(order);
    }
  });
});
