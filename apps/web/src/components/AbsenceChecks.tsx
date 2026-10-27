import { objectList, objectValue, textValue } from "../api/presentation";
import { t } from "../i18n/locale";

/** A declared string check is not evidence that something is absent from the whole document. */
export function AbsenceChecks({ result }: { result: Record<string, unknown> }) {
  const payload = objectValue(result.absence_assessments);
  const supportedChecker = payload.checker_version === "literal-absence:1.0.0" || payload.checker_version === "literal-absence:1.1.0";
  if (payload.schema_version !== "1.0.0" || !supportedChecker) return null;
  const active = textValue(objectValue(payload.active_stage_ref).revision_id);
  const items = objectList(payload.items).filter(item => !active || textValue(objectValue(item.origin_stage_ref).revision_id) === active);
  const outcome = (value: unknown) => value === "FOUND" ? t("absence.found") : value === "NOT_FOUND_WITHIN_SCOPE" ? t("absence.notFound") : value === "UNSUPPORTED" ? t("absence.unsupported") : t("absence.notChecked");
  const description = (value: unknown) => value === "FOUND_IN_MODEL_INPUT" ? t("absence.inInput") : value === "FOUND_OUTSIDE_MODEL_INPUT" ? t("absence.outsideInput") : value === "FOUND_A_SCOPE_UNCHECKED" ? t("absence.inputUnchecked") : value === "NOT_FOUND_WITHIN_SCOPES" ? t("absence.notFound") : value === "UNSUPPORTED" ? t("absence.unsupported") : t("absence.notChecked");
  return <section className="conversation-findings absence-checks" aria-label={t("absence.title")}>
    <h3>{t("absence.title")}</h3>
    <p className="muted">{t("absence.limitation")}</p>
    {items.length === 0 ? <p>{t("absence.empty")}</p> : <ul>{items.map((item, index) => {
      const claim = objectValue(item.claim);
      const target = textValue(item.target) || textValue(claim.target);
      const warning = typeof item.warning_code === "string" && item.warning_code.startsWith("FOUND_");
      return <li key={textValue(item.claim_id) || index}>
        <p role={warning ? "alert" : undefined}><strong>{description(item.effective)}</strong>{" · "}<code>{target}</code></p>
        <small>{t("absence.inputScope")}: {outcome(item.a_outcome ?? objectValue(item.a_result).outcome)}{" · "}{t("absence.allowedScope")}: {outcome(item.b_outcome ?? objectValue(item.b_result).outcome)}</small>
        {item.injection_state === "NOT_DELIVERED" && <p className="muted">{t("absence.notDelivered")}</p>}
      </li>;
    })}</ul>}
  </section>;
}
