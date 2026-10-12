import { uiText } from "../i18n/locale";
import { t } from "../i18n/locale";
import { Callout, Checkbox } from "@blueprintjs/core";
import { COMPLETE_LABEL, EFFECT_GROUPS, FORBIDDEN_NOTICE, prohibitedChosen } from "./effectDeclaration";
import { Disclosure } from "./Disclosure";

/** The folded list of what an action request would do. Every box starts empty; the person ticks what applies. */
export function EffectDeclarationFields({ chosen, complete, onChoose, onComplete }: {
  chosen: ReadonlySet<string>; complete: boolean; onChoose: (key: string, on: boolean) => void; onComplete: (on: boolean) => void;
}) {
  return <Disclosure label={t("effects.title")} className="effect-declaration">
    <small className="muted">{t("effects.rules")}</small>
    {EFFECT_GROUPS.map(group => <fieldset key={group.title}>
      <legend>{group.title}</legend>
      {group.effects.map(effect => <Checkbox key={effect.key} label={effect.label} checked={chosen.has(effect.key)} onChange={event => onChoose(effect.key, event.currentTarget.checked)}/>)}
    </fieldset>)}
    {prohibitedChosen(chosen).length > 0 && <Callout compact intent="warning" role="status">{uiText(FORBIDDEN_NOTICE)}</Callout>}
    <Checkbox label={uiText(COMPLETE_LABEL)} checked={complete} onChange={event => onComplete(event.currentTarget.checked)}/>
    {complete && chosen.size === 0 && <small className="muted">{t("effects.readOnly")}</small>}
  </Disclosure>;
}
