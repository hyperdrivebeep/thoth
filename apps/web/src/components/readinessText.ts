import { t } from "../i18n/locale";

/** One sentence about what blocks a new run, shared by the readiness card and the composer notice. */
export function blockedReason(
  consentMissing: boolean,
  modelConnected: boolean | undefined,
): string {
  if (consentMissing)
    return modelConnected === false ? t("composer.needBoth") : t("composer.needConsent");
  return modelConnected === false ? t("composer.needModel") : t("composer.needSetup");
}
