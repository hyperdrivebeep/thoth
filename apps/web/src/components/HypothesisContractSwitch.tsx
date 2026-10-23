import { t } from "../i18n/locale";
import { Switch } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";

type CallSettings = { auto_retry_interrupted_model_call: boolean; hypothesis_contract_v3: boolean; settings_digest: string | null };


/** Show the server's effective expected-results choice, including its default and saved overrides. */
export function HypothesisContractSwitch({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["model-call-settings", projectId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => rpc<CallSettings>("model/callSettings/read", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const change = useMutation({
    mutationFn: (enabled: boolean) => rpc<CallSettings>("model/callSettings/update", {
      project_id: projectId, hypothesis_contract_v3: enabled, expected_digest: query.data?.value.settings_digest ?? null,
    }, crypto.randomUUID()),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  if (query.isPending) return null;
  if (query.error || !query.data) return <p className="muted" role="status">{t("modelHelp.hypothesisReadFailed")}</p>;
  return <div className="hypothesis-contract-switch">
    <Switch checked={query.data.value.hypothesis_contract_v3 === true} disabled={change.isPending} label={t("modelHelp.hypothesisLabel")}
      onChange={event => change.mutate(event.currentTarget.checked)}/>
    <small className="muted">{t("modelHelp.hypothesis")}</small>
    {change.error && <small role="alert">{t("modelHelp.changeFailed")}</small>}
  </div>;
}
