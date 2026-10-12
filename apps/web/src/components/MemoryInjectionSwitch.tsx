import { t } from "../i18n/locale";
import { Switch } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";

type MemorySettings = { memory_injection: boolean; query_expansion?: boolean; settings_digest: string | null };


/** The project's switches: handing stored memory to an investigation, and widening its question. */
export function MemoryInjectionSwitch({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["memory-settings", projectId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => rpc<MemorySettings>("memory/settings/read", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const change = useMutation({
    mutationFn: (next: { injection?: boolean; expansion?: boolean }) => rpc<MemorySettings>("memory/settings/update", {
      project_id: projectId,
      memory_injection: next.injection ?? query.data?.value.memory_injection ?? true,
      // Only sent when the expansion switch itself changes; the project keeps its choice otherwise.
      ...(next.expansion === undefined ? {} : { query_expansion: next.expansion }),
      expected_digest: query.data?.value.settings_digest ?? null,
    }, crypto.randomUUID()),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  if (query.isPending) return null;
  if (query.error || !query.data) return <p className="muted" role="status">{t("memorySwitch.failed")}</p>;
  const enabled = query.data.value.memory_injection;
  const widening = query.data.value.query_expansion ?? true;
  return <div className="memory-switch" data-memory-switch>
    <Switch checked={enabled} disabled={change.isPending} label={t("memorySwitch.label")} onChange={event => change.mutate({ injection: event.currentTarget.checked })}/>
    <small className="muted">{t("memorySwitch.help")}</small>
    <Switch checked={widening} disabled={change.isPending || !enabled} label={t("memorySwitch.expansion")}
      onChange={event => change.mutate({ expansion: event.currentTarget.checked })} data-query-expansion-switch/>
    <small className="muted">{t("memorySwitch.expansionHelp")}</small>
    {change.error && <small role="alert">{t("modelHelp.changeFailed")}</small>}
  </div>;
}
