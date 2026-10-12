import { useEffect } from "react";
import { setLocale, applyDocumentLocale, useLocale, t, type Locale } from "../i18n/locale";

export function LanguageSettings() {
  const locale = useLocale();
  useEffect(() => applyDocumentLocale(locale), [locale]);
  return <div className="language-settings"><label>{t("language.display")}{" "}
    <select aria-label={t("language.display")} value={locale} onChange={event => setLocale(event.target.value as Locale)}>
      <option value="ko">한국어</option><option value="en">English</option>
    </select>
  </label></div>;
}
