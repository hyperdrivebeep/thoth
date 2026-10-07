import { Callout, Checkbox } from "@blueprintjs/core";
import { COMPLETE_LABEL, EFFECT_GROUPS, FORBIDDEN_NOTICE, prohibitedChosen } from "./effectDeclaration";
import { Disclosure } from "./Disclosure";

/** The folded list of what an action request would do. Every box starts empty; the person ticks what applies. */
export function EffectDeclarationFields({ chosen, complete, onChoose, onComplete }: {
  chosen: ReadonlySet<string>; complete: boolean; onChoose: (key: string, on: boolean) => void; onComplete: (on: boolean) => void;
}) {
  return <Disclosure label="이 행동이 하는 일 적기" className="effect-declaration">
    <small className="muted">적은 대로 규칙이 위험 등급과 필요한 사람을 정합니다. 아무것도 적지 않으면 효과를 확인해야 하는 행동으로 남습니다.</small>
    {EFFECT_GROUPS.map(group => <fieldset key={group.title}>
      <legend>{group.title}</legend>
      {group.effects.map(effect => <Checkbox key={effect.key} label={effect.label} checked={chosen.has(effect.key)} onChange={event => onChoose(effect.key, event.currentTarget.checked)}/>)}
    </fieldset>)}
    {prohibitedChosen(chosen).length > 0 && <Callout compact intent="warning" role="status">{FORBIDDEN_NOTICE}</Callout>}
    <Checkbox label={COMPLETE_LABEL} checked={complete} onChange={event => onComplete(event.currentTarget.checked)}/>
    {complete && chosen.size === 0 && <small className="muted">고른 것이 없으면 읽기만 하는 행동으로 적은 것이 됩니다.</small>}
  </Disclosure>;
}
