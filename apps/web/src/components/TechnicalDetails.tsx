import { t } from "../i18n/locale";

/** Internal ids, codes and untranslated originals of one part of a result. */
export type TechnicalGroup = { label: string; items: string[] };

/**
 * The one folded "technical details" block of a card. Each part of the card hands its ids and
 * originals here instead of folding them away next to itself, so the card has one such block.
 */
export function TechnicalDetails({ groups }: { groups: TechnicalGroup[] }) {
  const shown = groups.filter((group) => group.items.length > 0);
  if (shown.length === 0) return null;
  return (
    <details className="connection-tech result-technical">
      <summary>{t("oauth.technical")}</summary>
      {shown.map((group) => (
        <p key={group.label}>
          <span className="technical-label">{group.label}</span>{" "}
          <small>{group.items.join(" · ")}</small>
        </p>
      ))}
    </details>
  );
}
