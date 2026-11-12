import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { useResultLanguage } from "../i18n/ResponseLanguageContext";
import { useTranslation } from "../i18n/locale";
import { splitReadable } from "./plainWording";
import { foldedOriginals } from "./foldedOriginals";

/**
 * A list of texts the program or the model recorded: readable ones are listed, untranslated English is
 * only counted and folded away. A card that gathers its technical details in one place passes
 * technical={false} and shows foldedOriginals() there.
 */
export function ReadableList({
  items,
  technical: showTechnical = true,
}: {
  items: string[];
  technical?: boolean;
}) {
  useTranslation();
  const language = useResultLanguage();
  const { plain, technical } = splitReadable(items, language);
  const folded = showTechnical ? foldedOriginals(items, language) : [];
  return (
    <>
      {plain.length > 0 && (
        <ul>
          {plain.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      )}
      {technical.length > 0 && <p>{message("technicalPhrases", technical.length)}</p>}
      {folded.length > 0 && (
        <details className="connection-tech">
          <summary>{t("oauth.technical")}</summary>
          <small>{folded.join(" · ")}</small>
        </details>
      )}
    </>
  );
}
