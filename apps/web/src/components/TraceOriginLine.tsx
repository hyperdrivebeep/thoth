import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button } from "@blueprintjs/core";
import { objectValue, textValue } from "../api/presentation";

/** One line on a result that started from a trace row: which row, and a way back to it. */
export function TraceOriginLine({ result, onOpenTrace }: { result: Record<string, unknown>; onOpenTrace?: (row: { kind: string; id: string }) => void }) {
  const origin = objectValue(result.origin);
  const kind = textValue(origin.subject_kind);
  const id = textValue(origin.subject_id);
  if (origin.kind !== "TRACE_VERDICT" || !id || (kind !== "CRITERION" && kind !== "REQUIREMENT")) return null;
  return <p className="trace-origin-line" role="note">
    {formatMessage("traceOrigin", textValue(origin.subject_title) || id)}
    {onOpenTrace && <Button small minimal icon="th" onClick={() => onOpenTrace({ kind, id })}>{t("traceOrigin.open")}</Button>}
  </p>;
}
