import { useState } from "react";
import { t, useTranslation } from "../i18n/locale";
import { setDisplayPreference, useDisplayPreference, type DisplayPreference } from "../i18n/locale";
import { LANGUAGE_CHOICES, isLanguageCode } from "../i18n/languageChoices";
import { useResponseLanguageDefault, writeResponseLanguageDefault, type ResponseLanguageDefault } from "../api/responseLanguageDefault";
import type { BrowserScope } from "../api/localWorkspacePersistence";
import { useResponseLanguageSupport } from "./useResponseLanguageSupport";

/** Where the screen language and the default response language are chosen; the question box only shows a fixed one. */
export function LanguageSettingsCard({ scope }: { scope: BrowserScope }) {
  useTranslation();
  const display = useDisplayPreference();
  const reply = useResponseLanguageDefault(scope);
  const supported = useResponseLanguageSupport(scope);
  const [saveFailed, setSaveFailed] = useState(false);
  const chooseReply = (value: string) => {
    const next: ResponseLanguageDefault = value === "ko" || value === "en" ? value : null;
    setSaveFailed(!writeResponseLanguageDefault(scope, next));
  };
  return <section className="detail-card language-card" aria-labelledby="language-card-title">
    <h2 id="language-card-title">{t("language.cardTitle")}</h2>
    <div className="language-row">
      <label htmlFor="display-language">{t("language.display")}</label>
      <select id="display-language" value={display} onChange={event => setDisplayPreference((isLanguageCode(event.target.value) ? event.target.value : "auto") as DisplayPreference)}>
        <option value="auto">{t("language.displayAuto")}</option>
        {LANGUAGE_CHOICES.map(choice => <option key={choice.code} value={choice.code}>{choice.label}</option>)}
      </select>
    </div>
    <p className="muted">{t("language.displayHelp")}</p>
    <div className="language-row">
      <label htmlFor="reply-language-default">{t("language.replyDefault")}</label>
      <select id="reply-language-default" value={reply ?? "auto"} onChange={event => chooseReply(event.target.value)}>
        <option value="auto">{t("language.replyAuto")}</option>
        {LANGUAGE_CHOICES.map(choice => <option key={choice.code} value={choice.code} disabled={!supported}>{choice.label}</option>)}
      </select>
    </div>
    <p className="muted">{t("language.replyAutoHelp")}</p>
    {!supported && <p className="muted" role="status">{t("language.unsupported")}</p>}
    {saveFailed && <p className="muted" role="status">{t("language.saveFailed")}</p>}
    <p className="muted">{t("language.fixed")}</p>
    <p className="muted">{t("language.cost")}</p>
  </section>;
}
