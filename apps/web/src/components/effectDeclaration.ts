/**
 * What a person says an action request would do. The screen only collects it and sends it as written; the rules
 * on the server decide the risk from it, so nothing here works a risk out. Nothing is ticked for the person.
 */

export type EffectChoice = { key: string; label: string };
export type EffectGroup = { title: string; note?: string; effects: EffectChoice[] };

export const COMPLETE_KEY = "effect_completeness_confirmed";
export const COMPLETE_LABEL = "이 행동이 하는 일을 빠짐없이 적었습니다";

export const EFFECT_GROUPS: EffectGroup[] = [
  { title: "THOTH에서 실행할 수 없는 일", note: "고르면 실행할 수 없는 행동으로 분류됩니다.", effects: [
    { key: "changes_official_kpi", label: "공식 성과 지표를 바꿈" },
    { key: "grants_waiver", label: "면제를 내림" },
    { key: "changes_safety_threshold", label: "안전 기준값을 바꿈" },
    { key: "finalizes_model_weights", label: "모델 가중치를 확정함" },
  ] },
  { title: "사람의 승인이 필요한 일", effects: [
    { key: "external_write", label: "외부 시스템에 씀" },
    { key: "physical_action", label: "장비를 물리적으로 조작함" },
    { key: "changes_official_baseline", label: "공식 기준선을 바꿈" },
    { key: "operational_equipment_change", label: "운용 장비 설정을 바꿈" },
  ] },
  { title: "실행 환경", effects: [
    { key: "runs_untrusted_code", label: "검증 안 된 코드를 실행함" },
    { key: "sandbox_required", label: "격리된 환경이 필요함" },
  ] },
  { title: "그 밖에", effects: [
    { key: "changes_local_draft", label: "로컬 초안만 바꿈" },
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

export const FORBIDDEN_NOTICE = "이 효과는 THOTH에서 실행할 수 없는 행동으로 분류됩니다.";

