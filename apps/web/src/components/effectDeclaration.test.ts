import { expect, it } from "vitest";
import { EFFECT_GROUPS, effectDeclaration, prohibitedChosen } from "./effectDeclaration";

const ALL_KEYS = [
  "changes_official_kpi", "grants_waiver", "changes_safety_threshold", "finalizes_model_weights",
  "external_write", "physical_action", "changes_official_baseline", "operational_equipment_change",
  "runs_untrusted_code", "sandbox_required", "changes_local_draft",
];

it("lists every effect the server reads, with a plain label and no code value", () => {
  const keys = EFFECT_GROUPS.flatMap(group => group.effects.map(effect => effect.key));
  expect([...keys].sort()).toEqual([...ALL_KEYS].sort());
  for (const group of EFFECT_GROUPS) for (const effect of group.effects) expect(effect.label).not.toMatch(/[a-z]_[a-z]|R[0-4]/);
});

it("sends only what was chosen, and says the list is not complete, until the person confirms it is", () => {
  expect(effectDeclaration(new Set(), false)).toEqual({ effect_completeness_confirmed: false });
  expect(effectDeclaration(new Set(["grants_waiver"]), false)).toEqual({ grants_waiver: true, effect_completeness_confirmed: false });
});

it("names every effect as true or false once the person says the list is complete", () => {
  const none = effectDeclaration(new Set(), true);
  expect(Object.keys(none).sort()).toEqual([...ALL_KEYS, "effect_completeness_confirmed"].sort());
  expect(ALL_KEYS.every(key => none[key] === false)).toBe(true);
  expect(none.effect_completeness_confirmed).toBe(true);
  const some = effectDeclaration(new Set(["external_write", "physical_action"]), true);
  expect(Object.entries(some).filter(([, value]) => value).map(([key]) => key).sort()).toEqual(["effect_completeness_confirmed", "external_write", "physical_action"]);
});

it("finds the effects that no one may delegate", () => {
  expect(prohibitedChosen(new Set(["external_write"]))).toEqual([]);
  expect(prohibitedChosen(new Set(["external_write", "changes_official_kpi"]))).toEqual(["changes_official_kpi"]);
});
