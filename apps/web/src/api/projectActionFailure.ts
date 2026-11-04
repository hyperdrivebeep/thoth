import type { QueryClient } from "@tanstack/react-query";
import { t } from "../i18n/locale";
import { RpcError } from "./rpcClient";

const REVISION_MISMATCH = -32030;

/** An onError handler: a refused project action usually means the list is stale, so read it again. */
export const rereadProjects = (client: QueryClient) => () => { void client.invalidateQueries({ queryKey: ["projects"] }); };

/**
 * A refused project action (archive, restore) in words a person can act on. Re-evaluated on every
 * render so a language change applies. A revision mismatch means the screen held an older copy of
 * the project; the caller has already asked for the list again, and nothing is retried here.
 */
export function projectActionFailure(error: unknown, fallbackKey: "archive.archiveFailed" | "archive.restoreFailed"): string {
  if (error instanceof RpcError && error.code === REVISION_MISMATCH) return t("archive.revisionChanged");
  return t(fallbackKey);
}

