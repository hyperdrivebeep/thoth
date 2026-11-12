import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { RpcError } from "./rpcClient";

/** The server's answer to project/permanentDelete. cleanup_pending lists folders still owed. */
export type PermanentDeleteResult = {
  project_id: string;
  deleted: boolean;
  already_deleted: boolean;
  deleted_at: string;
  deleted_rows: number;
  cleanup_pending: string[];
};

const AUTHORIZATION_DENIED = -32040;
const PROJECT_NOT_FOUND = -32020;

/** A refused or failed deletion, in words a person can act on. Re-evaluated on every render so the language can change. */
export function permanentDeleteFailure(error: unknown): string {
  if (!(error instanceof RpcError)) return t("archive.deleteFailed");
  const reason = String(error.details.reason_code ?? "");
  if (reason === "PROJECT_NOT_ARCHIVED") return t("archive.deleteNotArchived");
  if (reason === "PROJECT_NAME_MISMATCH") return t("archive.deleteNameMismatch");
  if (reason === "PROJECT_HAS_ACTIVE_OPERATION" || reason === "PROJECT_EMBEDDING_JOB_RUNNING")
    return t("archive.deleteActive");
  if (reason === "PROJECT_REFERENCED_BY_OTHER_PROJECTS") {
    const ids = error.details.referencing_project_ids;
    return message("deleteReferenced", Array.isArray(ids) ? ids.map(String).join(", ") : "");
  }
  if (reason === "PROJECT_DELETE_LOCAL_ONLY") return t("archive.deleteLocalOnly");
  if (error.code === AUTHORIZATION_DENIED) return t("archive.deleteDenied");
  if (error.code === PROJECT_NOT_FOUND) return t("archive.deleteNotFound");
  return t("archive.deleteFailed");
}

export const projectAlreadyGone = (error: unknown) =>
  error instanceof RpcError && error.code === PROJECT_NOT_FOUND;
