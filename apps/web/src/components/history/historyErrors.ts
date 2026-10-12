import { t } from "../../i18n/locale";
import { RpcError } from "../../api/rpcClient";

export function restoreErrorText(error: Error): string {
  if (error instanceof RpcError) {
    const code = String(error.details.reason_code ?? "");
    if (/HEAD_CHANGED|PREVIEW_STALE/.test(code)) return t("restore.error.changed");
    if (/SOURCE_UNAVAILABLE|SOURCE_DRIFT/.test(code)) return t("restore.error.source");
    if (/ACCESS|AUTH|ROLE|SCOPE|REENTRY/.test(code)) return t("restore.error.access");
    if (/NOT_READY|SCHEMA_UNSUPPORTED|BLOCKED/.test(code)) return t("restore.error.unsupported");
    if (/IDEMPOTENCY/.test(code)) return t("restore.error.idempotency");
    return t("restore.error.unconfirmed");
  }
  if (error.name === "ZodError") return t("restore.error.schema");
  if (error instanceof TypeError || ["AbortError", "TimeoutError"].includes(error.name)) return t("restore.error.connection");
  return error.message;
}
