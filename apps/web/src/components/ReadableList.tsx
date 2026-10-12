import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { useResultLanguage } from "../i18n/ResponseLanguageContext";
import { useTranslation } from "../i18n/locale";
import { plainReason, splitReadable } from "./plainWording";

/** A list of texts the program or the model recorded: readable ones are listed, untranslated English is only counted and folded away. */
export function ReadableList({ items }: { items: string[] }) {
  useTranslation();
  const { plain, technical } = splitReadable(items, useResultLanguage());
  const folded = [...items.filter(item => plainReason(item) !== null), ...technical];
  return <>
    {plain.length > 0 && <ul>{plain.map((item, index) => <li key={index}>{item}</li>)}</ul>}
    {technical.length > 0 && <p>{message("technicalPhrases", technical.length)}</p>}
    {folded.length > 0 && <details className="connection-tech"><summary>{t("oauth.technical")}</summary><small>{folded.join(" · ")}</small></details>}
  </>;
}
