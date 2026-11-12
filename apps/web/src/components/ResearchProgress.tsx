import { message } from "../i18n/formatMessages";
import { localeTag } from "../i18n/locale";
import { t, useTranslation } from "../i18n/locale";
import { Button, Callout } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ResearchStatus } from "../api/research";
import { composerActivity } from "../api/researchProgress";
import { rpc } from "../api/rpcClient";

export function ResearchProgress({ status }: { status: ResearchStatus }) {
  useTranslation();
  const client = useQueryClient();
  const control = useMutation({ mutationFn: (method: string) => rpc<Record<string, unknown>>(method,
    { project_id: status.project_id, thread_id: status.thread_id }, crypto.randomUUID()),
    onSuccess: () => Promise.all([client.invalidateQueries({queryKey:["research",status.project_id,status.thread_id]}), client.invalidateQueries({queryKey:["threads",status.project_id]})]),
  });
  const message = composerActivity(status);
  const paused = status.execution_state === "PAUSED";
  const running = status.operation_state === "RUNNING";
  // Nothing is running and nothing is to be said: no empty group is left in the question box.
  if (!message && !running && !paused && !control.error) return null;
  return <div className="activity-disclosure" role="group" aria-label={t("progress.current")}>
    {message && <p role="status"><span className={paused ? "activity-dot paused" : "activity-dot"}/>{message}</p>}
    <div className="activity-controls">
      {running && !paused && <Button small minimal icon="pause" disabled={control.isPending} onClick={()=>control.mutate("thread/pause")}>{t("progress.pause")}</Button>}
      {paused && <Button small minimal icon="play" disabled={control.isPending} onClick={()=>control.mutate("thread/resume")}>{t("progress.resume")}</Button>}
      {running && <Button small minimal icon="stop" disabled={control.isPending} onClick={()=>{if(window.confirm(t("progress.stopConfirm")))control.mutate("thread/stop");}}>{t("progress.stop")}</Button>}
    </div>
    {control.error && <Callout compact intent="danger" role="alert">{control.error.message}</Callout>}
  </div>;
}
export function UsageSummary({status}: {status?: Pick<ResearchStatus, "usage">}) {
  useTranslation();
  const usage=status?.usage;
  const number=(value:number|null|undefined)=>value == null ? t("progress.unknown") : value.toLocaleString(localeTag());
  return <span className="usage-summary" title={message("usageTooltip", number(usage?.input_tokens), number(usage?.output_tokens))}>
    {message("usageCaption", number(usage?.total_tokens), usage?.state === "PARTIAL")}
  </span>;
}
