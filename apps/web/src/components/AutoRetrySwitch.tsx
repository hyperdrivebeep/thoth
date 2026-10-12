import { t } from "../i18n/locale";
import { Switch } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";

type CallSettings = { auto_retry_interrupted_model_call: boolean; settings_digest: string | null };


/** The project's switch for sending a cut-off model call again (at most twice). It shows what the server says. */
export function AutoRetrySwitch({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["model-call-settings", projectId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => rpc<CallSettings>("model/callSettings/read", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const change = useMutation({
    mutationFn: (enabled: boolean) => rpc<CallSettings>("model/callSettings/update", {
      project_id: projectId, auto_retry_interrupted_model_call: enabled, expected_digest: query.data?.value.settings_digest ?? null,
    }, crypto.randomUUID()),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  if (query.isPending) return null;
  if (query.error || !query.data) return <p className="muted" role="status">{t("modelHelp.retryReadFailed")}</p>;
  return <div className="auto-retry-switch" data-auto-retry-switch>
    <Switch checked={query.data.value.auto_retry_interrupted_model_call} disabled={change.isPending} label={t("modelHelp.retryLabel")}
      onChange={event => change.mutate(event.currentTarget.checked)}/>
    <small className="muted">{t("modelHelp.retry")}</small>
    {change.error && <small role="alert">{t("modelHelp.changeFailed")}</small>}
  </div>;
}

