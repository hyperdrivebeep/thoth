import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { TraceView, Verdict } from "../api/trace";
import { reasonTags } from "./traceReasonTags";
import { NEXT_STEP_DECIDERS, nextStep } from "./traceText";

/**
 * The tags and the next-check wording, scored on the synthetic cases the server tests also read
 * (tests/fixtures/hypothesis_eval/cases.json). The Python side proves each stored view is what the rule
 * computes and that the states and reason codes match the answer key; this side scores what the screen
 * derives from that view. No model is involved.
 */

type Check = { kind: "CRITERION" | "REQUIREMENT"; subject_id: string; state: string; tags: string[]; next_step: boolean; stale: boolean };
type Case = { id: string; title: string; view: TraceView; checks: Check[]; banned_phrases: string[]; synthetic_notice: string };
const file = JSON.parse(readFileSync(new URL("../../../../tests/fixtures/hypothesis_eval/cases.json", import.meta.url), "utf8")) as { cases: Case[] };
const LEAK = /HOLD_|FAIL_|PASS_|[A-Za-z]+_[A-Za-z]+/;

/** The role each tag's own next check names (provisional role names). */
const DECIDER_OF_TAG: Partial<Record<string, string>> = {
  UNTESTED: NEXT_STEP_DECIDERS.test, UNLINKED: NEXT_STEP_DECIDERS.rule, CONDITION_MISMATCH: NEXT_STEP_DECIDERS.test,
  UNIT: NEXT_STEP_DECIDERS.validity, DENOMINATOR: NEXT_STEP_DECIDERS.validity, VALUE: NEXT_STEP_DECIDERS.validity,
};

const verdictOf = (item: Case, check: Check) => {
  const found = item.view.verdicts.filter(verdict => verdict.subject_kind === check.kind && verdict.subject_id === check.subject_id);
  expect(found, item.id + " " + check.subject_id).toHaveLength(1);
  return found[0] as Verdict;
};
const wordsOf = (item: Case, check: Check) => {
  const verdict = verdictOf(item, check);
  const stale = verdict.currentness.state === "STALE_BASIS";
  const tags = reasonTags(check.kind, verdict, item.view);
  const next = nextStep(check.kind, verdict.state, stale, tags.map(tag => tag.id));
  return { verdict, stale, tags, next, text: [...tags.flatMap(tag => [tag.label, tag.hint]), ...(next ? next.lines.flatMap(item => [item.text, item.decider ?? ""]) : [])] };
};

describe("tags and next checks on the synthetic cases", () => {
  it("has the agreed cases, all marked synthetic", () => {
    expect(file.cases.length).toBeGreaterThanOrEqual(10);
    for (const item of file.cases) expect(item.synthetic_notice).toContain("SYNTHETIC DEMO DATA");
  });

  for (const item of file.cases) {
    it(item.id + ": " + item.title, () => {
      for (const check of item.checks) {
        const { stale, tags, next, text } = wordsOf(item, check);
        expect(tags.map(tag => tag.id), check.subject_id).toEqual(check.tags);
        expect(stale, check.subject_id).toBe(check.stale);
        expect(next !== null, check.subject_id + " next check").toBe(check.next_step);
        if (check.stale && next) expect(next.lines[0].text).toContain("이전 안내는 이전 근거 기준입니다");
        // a held criterion with a tag that has its own check gets that check, decided by the named role
        const own = tags.map(tag => DECIDER_OF_TAG[tag.id]).filter((item): item is string => item !== undefined);
        if (check.kind === "CRITERION" && own.length > 0) {
          const named = next!.lines.filter(item => item.decider !== null).map(item => item.decider);
          expect(named, check.subject_id).toEqual(own);
        }
        for (const line of text) {
          expect(line).not.toMatch(LEAK);
          for (const banned of item.banned_phrases) expect(line).not.toContain(banned);
        }
      }
    });
  }

  it("scores every case: tags exactly right, no passed line carries a tag or a next check, nothing unexplained is held", () => {
    let checks = 0;
    let tagMisses = 0;
    let passedWithTag = 0;
    let heldWithoutWords = 0;
    for (const item of file.cases) {
      for (const check of item.checks) {
        const { verdict, tags, next } = wordsOf(item, check);
        checks += 1;
        if (tags.map(tag => tag.id).join() !== check.tags.join()) tagMisses += 1;
        const met = verdict.state === "PASS_COMPUTED" || verdict.state === "PASS";
        if (met && verdict.currentness.state === "CURRENT" && (tags.length > 0 || next !== null)) passedWithTag += 1;
        const held = verdict.state.startsWith("HOLD_");
        if (held && tags.length === 0 && check.kind === "CRITERION") heldWithoutWords += 1;
      }
    }
    expect(checks).toBeGreaterThanOrEqual(25);
    expect({ tagMisses, passedWithTag, heldWithoutWords }).toEqual({ tagMisses: 0, passedWithTag: 0, heldWithoutWords: 0 });
  });
});
