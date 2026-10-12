import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout, Classes, Dialog, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { rpc, RpcError } from "../api/rpcClient";
import type { ExecutionHold } from "../api/research";

/** The words for a refusal of "execution/invalidate" (STALE_CHECKPOINT -32031, DOMAIN_REJECTED -32030). */
function refusal(error: unknown): string {
  if (error instanceof RpcError && error.code === -32031) return t("hold.stateChanged");
  if (error instanceof RpcError && error.code === -32030) return t("hold.cannotClear");
  return t("hold.clearFailed");
}

function inputSentence(hold: ExecutionHold): string | null {
  const diff = hold.input_difference;
  if (!diff) return null;
  const previous = diff.previous_count ?? 0;
  const current = diff.current_count ?? 0;
  if (diff.same) return formatMessage("inputSame", current);
  return formatMessage("inputDifference", previous, current, diff.removed?.length ?? 0, diff.added?.length ?? 0);
}

/** A closed-loop test that was held. When an earlier run is unsettled, the one way out is t("hold.clearAndRerun"): clear it with a reason; the question is then sent again by the person. */
export function ExecutionHoldNotice({ hold, projectId, causeRef, onCleared }: { hold: ExecutionHold; projectId: string; causeRef: string; onCleared?: () => void }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [cleared, setCleared] = useState(false);
  const sending = useRef(false); // set at once, so a second press before the screen updates sends nothing
  const clearable = hold.exits.includes("CLEAR_AND_RERUN") && hold.plan_execution_id !== null && hold.attempt_id !== null && hold.execution_revision !== null;
  const clear = useMutation({
    mutationFn: (why: string) => rpc("execution/invalidate", {
      project_id: projectId, plan_execution_id: hold.plan_execution_id, cause_revision_ref: causeRef, impact_refs: [],
      clear_pending_attempt_id: hold.attempt_id, expected_execution_revision: hold.execution_revision, reason: why, input_difference: hold.input_difference,
    }, crypto.randomUUID()),
    onSettled: () => { sending.current = false; },
    onSuccess: async () => {
      setCleared(true); setOpen(false); setReason(""); setMessage("");
      await client.invalidateQueries({ queryKey: ["research", projectId] });
      onCleared?.();
    },
    onError: async error => {
      setMessage(refusal(error));
      await client.invalidateQueries({ queryKey: ["research", projectId] });
    },
  });
  const submit = () => {
    if (sending.current) return;
    if (!reason.trim()) { setMessage(t("hold.reasonNeeded")); return; }
    setMessage("");
    sending.current = true;
    clear.mutate(reason.trim());
  };
  const technical = [hold.reason_code, hold.plan_execution_id, hold.execution_revision !== null ? "revision " + hold.execution_revision : null, hold.attempt_id, hold.attempt_state,
    ...(hold.input_difference?.removed ?? []).map(item => "removed " + item), ...(hold.input_difference?.added ?? []).map(item => "added " + item)].filter(Boolean);
  const technicalInfo = technical.length > 0 && <details className="connection-tech"><summary>{t("oauth.technical")}</summary><small>{technical.join(" · ")}</small></details>;
  if (!clearable) {
    return <div className="result-notice" role="status">
      <p><strong>{t("hold.notRun")}</strong>{t("hold.notRunNotice")}</p>
      {technicalInfo}
    </div>;
  }
  const difference = inputSentence(hold);
  return <div className="result-notice" role="status">
    <p><strong>{t("hold.notRepeated")}</strong>{t("hold.notRepeatedNotice")}</p>
    {difference && <p>{difference}</p>}
    {cleared
      ? <p>{t("hold.cleared")}{onCleared ? t("hold.draftReady") : ""}</p>
      : <>
        <p>{t("hold.choice")}</p>
        <Button small icon="refresh" onClick={() => { setMessage(""); setOpen(true); }}>{t("hold.clearAndRerun")}</Button>
        <small className="muted">{t("hold.notImmediate")}</small>
      </>}
    {technicalInfo}
    <Dialog isOpen={open} onClose={() => { if (!clear.isPending) setOpen(false); }} title={t("hold.clearAndRerun")}>
      <div className={Classes.DIALOG_BODY}>
        <p>{t("hold.clearNotice")}</p>
        <label className="review-note">{t("hold.reasonRequired")}<TextArea fill rows={3} aria-label={t("hold.reasonLabel")} value={reason} maxLength={2000} onChange={event => setReason(event.target.value)}/></label>
        <small className="muted">{t("hold.reasonExample")}</small>
        {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
      </div>
      <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
        <Button disabled={clear.isPending} onClick={() => setOpen(false)}>{t("hold.close")}</Button>
        <Button intent="primary" disabled={clear.isPending} loading={clear.isPending} onClick={submit}>{t("hold.clear")}</Button>
      </div></div>
    </Dialog>
  </div>;
}
