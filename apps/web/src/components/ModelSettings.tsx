import { message } from "../i18n/formatMessages";
import { t, useTranslation } from "../i18n/locale";
import { Button, HTMLSelect } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { rpc } from "../api/rpcClient";
import { providerLabel } from "./statusLabels";
import { catalogLine, excludedModels, excludedReasonLabels, optionText, staleCatalogNotice, type CatalogStatusRow } from "./modelCatalogPresentation";

export type ModelSelection = { provider: string; model: string; reasoning_effort?: string };
type ModelOption = { provider: string; model: string; reasoning_efforts: string[]; default_effort: string | null;   entitlement?: string; execution?: string; label?: string | null };
type SavedSelection = { provider: string | null; model: string | null; reasoning_effort?: string | null };
type EffectiveSettings = { provider: string; model: string | null; reasoning_effort?: string | null };
type Settings = { settings_digest: string | null; selection?: SavedSelection; effective_settings: EffectiveSettings | null;
  availability?: "AVAILABLE" | "UNAVAILABLE"; reason_code?: string | null; model_options: ModelOption[]; catalog_status?: CatalogStatusRow[] };

function unavailableReason(code?: string | null): string {
  if (code === "MODEL_CAPABILITY_UNKNOWN") return t("model.missingCapability");
  if (code === "MODEL_REASONING_EFFORT_UNSUPPORTED") return t("model.unsupportedEffort");
  if (code === "MODEL_ROUTE_INCOMPLETE") return t("model.incompleteRoute");
  return code ? message("unavailableCode", code) : t("model.unknownReason");
}

function savedLabel(value?: SavedSelection): string | null {
  if (!value?.provider && !value?.model && !value?.reasoning_effort) return null;
  return `${value.provider ?? t("model.providerUnset")} / ${value.model ?? t("model.modelUnset")}${value.reasoning_effort ? message("effort", value.reasoning_effort) : ""}`;
}

export function ModelSettings({ projectId, threadId, selection, onSelect, onSaved, compact = false }: {
  projectId: string; threadId?: string; selection: ModelSelection | null;
  onSelect: (selection: ModelSelection) => void; onSaved: () => void; compact?: boolean;
}) {
  useTranslation();
  const client = useQueryClient();
  const [explicitReplacement, setExplicitReplacement] = useState<{ scope: string; readAt: number; choice: ModelSelection } | null>(null);
  const scope = { project_id: projectId, ...(threadId ? { thread_id: threadId } : {}) };
  const scopeIdentity = JSON.stringify([projectId, threadId ?? null]);
  const key = ["model-settings", projectId, threadId];
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) =>
    rpc<Settings>("model/settings/read", scope, crypto.randomUUID(), signal) });
  const data = query.isFetching || query.error ? undefined : query.data?.value;
  const effective = data?.effective_settings;
  const saved = data?.selection;
  const inheritsProject = Boolean(threadId && saved && !saved.provider && !saved.model && !saved.reasoning_effort);
  const inherited = useQuery({ queryKey: ["model-settings", projectId, threadId, "project-parent", query.dataUpdatedAt], enabled: inheritsProject && data?.availability === "UNAVAILABLE",
    queryFn: ({ signal }) => rpc<Settings>("model/settings/read", { project_id: projectId }, crypto.randomUUID(), signal), retry: false,
    gcTime: 0, refetchOnMount: "always" });
  const inheritedLabel = savedLabel(inherited.data?.value.selection);
  const savedChoice = saved?.provider && saved.model ? { provider: saved.provider, model: saved.model,
    ...(saved.reasoning_effort ? { reasoning_effort: saved.reasoning_effort } : {}) } : undefined;
  const effectiveChoice = effective?.model ? { provider: effective.provider, model: effective.model,
    ...(effective.reasoning_effort ? { reasoning_effort: effective.reasoning_effort } : {}) } : undefined;
  const chosenReplacement = explicitReplacement?.scope === scopeIdentity && explicitReplacement.readAt === query.dataUpdatedAt
    ? explicitReplacement.choice : null;
  const current = data?.availability === "UNAVAILABLE" ? chosenReplacement ?? savedChoice : selection ?? effectiveChoice ?? savedChoice;
  const option = data?.model_options.find(o => o.provider === current?.provider && o.model === current?.model);
  const validEffort = data?.availability === "UNAVAILABLE" && chosenReplacement && option?.reasoning_efforts.length
    ? Boolean(current?.reasoning_effort && option.reasoning_efforts.includes(current.reasoning_effort))
    : !current?.reasoning_effort || Boolean(option?.reasoning_efforts.includes(current.reasoning_effort));
  const explicitlyChosen = data?.availability !== "UNAVAILABLE" || Boolean(chosenReplacement);
  const catalogRows = data?.catalog_status;
  const catalogRow = catalogRows?.find(row => row.provider === (option?.provider ?? current?.provider)) ?? catalogRows?.[0];
  const excluded = excludedModels(catalogRows);
  const choose = (next: ModelSelection) => {
    const chosen = { provider: next.provider, model: next.model, reasoning_effort: next.reasoning_effort };
    if (data?.availability === "UNAVAILABLE") setExplicitReplacement({ scope: scopeIdentity, readAt: query.dataUpdatedAt, choice: chosen });
    onSelect(chosen);
  };
  const save = useMutation({ mutationFn: async (captured: {
    scope: typeof scope; selection: ModelSelection; digest: string | null;
    key: typeof key; onSaved: () => void;
  }) => {
    const response = await rpc<Settings>("model/settings/update", {
      ...captured.scope, selection: captured.selection, expected_digest: captured.digest,
    }, crypto.randomUUID());
    return {response, captured};
  }, onSuccess: async ({response, captured}) => {
    client.setQueryData(captured.key, response);
    await client.invalidateQueries({ queryKey: captured.scope.thread_id
      ? captured.key : ["model-settings", captured.scope.project_id] });
    captured.onSaved();
  } });
  return <fieldset className={`model-settings ${compact ? "compact" : ""}`}>
    <legend>{t("model.settings")}</legend>
    {data?.availability === "UNAVAILABLE" && <p role="status" className="result-notice">{t("model.unavailable")} {unavailableReason(data.reason_code)}
      {savedLabel(saved) ? message("savedSelection", savedLabel(saved) ?? "") : ""}
      {inheritsProject ? t("model.inheritedUnchanged") : ""}
      {" "}{t("model.keepSelection")}</p>}
    {inheritsProject && data?.availability === "UNAVAILABLE" && (inherited.isPending || inherited.isFetching ? <small role="status">{t("model.parentLoading")}</small>
      : inherited.error ? <small role="alert">{t("model.parentFailed")}</small>
        : <small>{message("inheritedSelection", inheritedLabel ?? t("model.noProjectModel"))}</small>)}
    {inheritsProject && data?.availability !== "UNAVAILABLE" && <small>{t("model.inheritsProject")}</small>}
    <label><span className="model-control-label">{t("model.modelLabel")}</span> <HTMLSelect key={`${scopeIdentity}:${query.dataUpdatedAt}`} aria-label={t("model.researchModel")} disabled={!data} value={option ? `${option.provider}/${option.model}` : ""}
      onChange={event => {
        const next = data?.model_options.find(o => `${o.provider}/${o.model}` === event.target.value);
        if (next) choose({ provider: next.provider, model: next.model,
          ...(data?.availability !== "UNAVAILABLE" && next.default_effort ? { reasoning_effort: next.default_effort } : {}) });
      }}><option value="">{t("model.choose")}</option>{data?.model_options.map(o =>
        <option key={`${o.provider}/${o.model}`} value={`${o.provider}/${o.model}`}>{optionText(o, providerLabel(o.provider))}</option>)}</HTMLSelect></label>
    <label><span className="model-control-label">{t("model.effortLabel")}</span> <HTMLSelect aria-label={t("model.reasoningEffort")} disabled={!option} value={current?.reasoning_effort ?? ""}
      onChange={event => { if (current) choose({ ...current, reasoning_effort: event.target.value }); }}>
      <option value="">{t("model.default")}</option>{option?.reasoning_efforts.map(e => <option key={e} value={e}>{e}</option>)}</HTMLSelect></label>
    {!compact && <Button className="model-defaults" small minimal icon="floppy-disk" disabled={!current || !option || !validEffort || !explicitlyChosen || !data || save.isPending} loading={save.isPending} onClick={() => {
      if (current && option && validEffort && explicitlyChosen && data) save.mutate({scope, selection: current, digest: data.settings_digest, key, onSaved});
    }}>
      {threadId ? t("model.saveThread") : t("model.saveProject")}</Button>}
    {catalogRow && <small data-catalog-status>{catalogLine(catalogRow)}</small>}
    {catalogRow?.status === "STALE_LAST_GOOD" && <small role="status" className="result-notice">{staleCatalogNotice()}</small>}
    {excluded.length > 0 && <details className="model-excluded"><summary>{message("excludedModels", excluded.length)}</summary>
      {excluded.map(item => <small key={`${item.provider}/${item.model}`}>{item.model} · {excludedReasonLabels[item.reason] ?? item.reason}</small>)}</details>}
    <small>{t("model.nextRequestOnly")}</small>
    {save.isSuccess && <small role="status">{t("model.saved")}</small>}
    {(query.error || save.error) && <span role="alert">{(query.error ?? save.error)?.message}</span>}
  </fieldset>;
}
