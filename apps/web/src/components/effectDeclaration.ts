import { messages } from "../i18n/messages";
import { t } from "../i18n/locale";
/**
 * What a person says an action request would do. The screen only collects it and sends it as written; the rules
 * on the server decide the risk from it, so nothing here works a risk out. Nothing is ticked for the person.
 */

export type EffectChoice = { key: string; label: string };
export type EffectGroup = { title: string; note?: string; effects: EffectChoice[] };

export const COMPLETE_KEY = "effect_completeness_confirmed";
export const COMPLETE_LABEL = messages["effects.complete"].ko;

export const EFFECT_GROUPS: EffectGroup[] = [
  { get title() { return t("effects.prohibited"); }, get note() { return t("effects.prohibitedHint"); }, effects: [
    { key: "changes_official_kpi", get label() { return t("effects.kpi"); } },
    { key: "grants_waiver", get label() { return t("effects.waiver"); } },
    { key: "changes_safety_threshold", get label() { return t("effects.safety"); } },
    { key: "finalizes_model_weights", get label() { return t("effects.weights"); } },
  ] },
  { get title() { return t("effects.human"); }, effects: [
    { key: "external_write", get label() { return t("effects.externalWrite"); } },
    { key: "physical_action", get label() { return t("effects.hardware"); } },
    { key: "changes_official_baseline", get label() { return t("effects.baseline"); } },
    { key: "operational_equipment_change", get label() { return t("effects.configuration"); } },
  ] },
  { get title() { return t("effects.environment"); }, effects: [
    { key: "runs_untrusted_code", get label() { return t("effects.code"); } },
    { key: "sandbox_required", get label() { return t("effects.sandbox"); } },
  ] },
  { get title() { return t("effects.other"); }, effects: [
    { key: "changes_local_draft", get label() { return t("effects.localDraft"); } },
  ] },
];

const FORBIDDEN = EFFECT_GROUPS[0].effects.map(effect => effect.key);

/** The chosen effects that THOTH cannot run (shown as a notice before sending; the server still decides). */
export function prohibitedChosen(chosen: ReadonlySet<string>): string[] {
  return FORBIDDEN.filter(key => chosen.has(key));
}

/**
 * The declaration to send. Once the person says the list is complete, every effect is named true or false; before
 * that only the chosen ones are sent, and the list is said not to be complete.
 */
export function effectDeclaration(chosen: ReadonlySet<string>, complete: boolean): Record<string, boolean> {
  const keys = EFFECT_GROUPS.flatMap(group => group.effects.map(effect => effect.key));
  const named = complete ? keys : keys.filter(key => chosen.has(key));
  return { ...Object.fromEntries(named.map(key => [key, chosen.has(key)])), [COMPLETE_KEY]: complete };
}

export const FORBIDDEN_NOTICE = messages["effects.blocked"].ko;

