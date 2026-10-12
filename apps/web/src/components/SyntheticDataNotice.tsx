import { messages } from "../i18n/messages";
import { t } from "../i18n/locale";
import { Callout } from "@blueprintjs/core";
import { useQuery } from "@tanstack/react-query";
import { readTrace, traceKey } from "../api/trace";
import { isSyntheticTrace } from "./traceRows";

export const SYNTHETIC_LINE = messages["synthetic.notice"].ko;

/**
 * A thin line above the conversation when the project's trace table was written as demo data. It reads the trace with the
 * same query the trace page uses (one existing read, shared cache); no table, an unreadable table, or no marker shows nothing.
 */
export function SyntheticDataNotice({ projectId }: { projectId: string }) {
  const query = useQuery({ queryKey: traceKey(projectId), enabled: Boolean(projectId), retry: false, staleTime: 30_000, queryFn: ({ signal }) => readTrace(projectId, signal) });
  const view = query.data;
  if (!view || !Array.isArray(view.items) || !isSyntheticTrace(view)) return null;
  return <Callout compact intent="warning" icon="info-sign" role="note" className="conversation-synthetic-notice">{t("synthetic.notice")}</Callout>;
}
