import { objectList } from "../api/presentation";
import { useTranslation } from "../i18n/locale";

export function GroundingRowsNotice({ result }: { result: Record<string, unknown> }) {
  const t = useTranslation();
  return objectList(result.hypothesis_grounding_diagnostics).length > 0
    ? <p className="result-notice" role="status">{t("grounding.omitted")}</p> : null;
}

export function GroundingHoldNotice({ hasSavedResult = false }: { hasSavedResult?: boolean }) {
  const t = useTranslation();
  return <div className="result-notice" role="alert"><p>{t("grounding.hold")}
    {hasSavedResult && <> {t("grounding.saved")}</>}</p></div>;
}
