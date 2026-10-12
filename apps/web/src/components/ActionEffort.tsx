import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, HTMLSelect, InputGroup } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { objectValue, textValue } from "../api/presentation";
import { rpc } from "../api/rpcClient";
import { parseEstimates, type Estimate } from "./effortEstimates";
import { researchRunningNotice } from "./memoryEdit";
import { effortBandLabel, effortBandOptions, effortDimensionLabel, estimatorLabel } from "./statusLabels";

const DIMENSIONS = ["TIME", "COST_EFFORT"] as const;
const HUMAN_REF = "human:local-user";

const latest = (estimates: Estimate[], dimension: string, type: string) =>
  [...estimates].reverse().find(item => item.dimension === dimension && item.estimator_type === type);

function DimensionLine({ dimension, estimates }: { dimension: string; estimates: Estimate[] }) {
  const ai = latest(estimates, dimension, "AI");
  const human = latest(estimates, dimension, "HUMAN");
  const name = effortDimensionLabel(dimension);
  const band = (item: Estimate) => effortBandLabel(dimension, item.band);
  if (ai && human && ai.band !== human.band) {
    return <p className="effort-line">{name}: <span>{estimatorLabel("AI")}: {band(ai)}</span> / <span>{estimatorLabel("HUMAN")}: {band(human)}</span>
      {human.basis_text && <small> · {human.basis_text}</small>}{ai.basis_text && <small>{" "}{t("labels.aiBasis")}{" "}{ai.basis_text}</small>}</p>;
  }
  if (ai && human) {
    return <p className="effort-line">{name}: {band(human)}{" "}{t("labels.estimatesAgree")}{human.basis_text && <small> · {human.basis_text}</small>}</p>;
  }
  const only = ai ?? human;
  if (!only || only.band === "UNKNOWN") return <p className="effort-line">{name}{t("labels.unknownCheck")}{only?.basis_text && <small> · {only.basis_text}</small>}</p>;
  return <p className="effort-line">{name}: {band(only)} · {estimatorLabel(only.estimator_type)}{only.basis_text && <> · {only.basis_text}</>}</p>;
}

/** Time and cost-effort bands for one action. Bands are labels only; nothing here adds, ranks or converts them. */
export function EffortLines({ estimates, legacy }: { estimates: Estimate[]; legacy?: string }) {
  if (estimates.length === 0) return <p>{t("labels.costTime")}{" "}{legacy ?? t("state.unknown")}</p>;
  return <div className="effort-lines">{DIMENSIONS.map(dimension => <DimensionLine key={dimension} dimension={dimension} estimates={estimates}/>)}</div>;
}

type Draft = Record<string, { band: string; basis: string }>;
const emptyDraft = (): Draft => ({ TIME: { band: "", basis: "" }, COST_EFFORT: { band: "", basis: "" } });

/** Shows the record's current estimates and lets a person add their own as a new revision. */
export function ActionEffort({ projectId, actionId, initial, legacy }: { projectId: string; actionId: string; initial: unknown; legacy?: string }) {
  const client = useQueryClient();
  const key = ["action-effort", projectId, actionId];
  const record = useQuery({ queryKey: key, retry: false,
    queryFn: ({ signal }) => rpc<{ action: Record<string, unknown>; research_running?: boolean }>("action/read", { project_id: projectId, action_id: actionId }, crypto.randomUUID(), signal),
    refetchInterval: current => current.state.data?.value.research_running ? 3000 : false });
  const running = record.data?.value.research_running === true;
  const action = objectValue(record.data?.value.action);
  const details = objectValue(action.generation_details);
  const estimates = parseEstimates(record.data && Array.isArray(details.effort_estimates) ? details.effort_estimates : initial);
  const digest = textValue(action.revision_digest);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [message, setMessage] = useState("");
  const save = useMutation({
    mutationFn: (entries: { dimension: string; band: string; basis_text: string }[]) => rpc("action/revise", {
      project_id: projectId, action_id: actionId, expected_revision_digest: digest, patch: {}, evidence_refs: [],
      get reason() { return t("effort.humanEntry"); }, estimator_ref: HUMAN_REF, human_effort_estimates: entries }, crypto.randomUUID()),
    onSuccess: async () => { setDraft(emptyDraft()); setOpen(false); setMessage(""); await client.invalidateQueries({ queryKey: key }); },
  });
  const submit = () => {
    const entries = DIMENSIONS.filter(dimension => draft[dimension].band).map(dimension => ({ dimension, band: draft[dimension].band, basis_text: draft[dimension].basis.trim() }));
    if (entries.length === 0) { setMessage(t("effort.choose")); return; }
    if (entries.some(entry => entry.band !== "UNKNOWN" && !entry.basis_text)) { setMessage(t("effort.basisNeeded")); return; }
    setMessage(""); save.mutate(entries);
  };
  const set = (dimension: string, part: "band" | "basis", value: string) => setDraft(current => ({ ...current, [dimension]: { ...current[dimension], [part]: value } }));
  return <div className="action-effort">
    <EffortLines estimates={estimates} legacy={legacy}/>
    <Button small minimal icon={open ? "chevron-up" : "edit"} aria-label={t("effort.open")} aria-expanded={open} disabled={!digest || running} onClick={() => setOpen(value => !value)}>{t("effort.humanEntry")}</Button>
    {running && <small className="muted">{researchRunningNotice}</small>}
    {open && <div className="effort-editor">
      {DIMENSIONS.map(dimension => <div className="effort-editor-row" key={dimension}>
        <label>{effortDimensionLabel(dimension)}
          <HTMLSelect aria-label={formatMessage("effortEstimate", effortDimensionLabel(dimension))} value={draft[dimension].band} onChange={event => set(dimension, "band", event.target.value)}
            options={[{ value: "", get label() { return t("effort.none"); } }, ...effortBandOptions(dimension)]}/></label>
        <InputGroup small aria-label={formatMessage("effortBasis", effortDimensionLabel(dimension))} placeholder={t("effort.basis")} value={draft[dimension].basis} onChange={event => set(dimension, "basis", event.target.value)}/>
      </div>)}
      <small className="muted">{t("effort.preserveAI")}</small>
      <Button small intent="primary" aria-label={t("effort.saveHuman")} loading={save.isPending} onClick={submit}>{t("effort.save")}</Button>
      {message && <p role="alert">{message}</p>}
      {save.error && <p role="alert">{t("effort.failed")}{" "}{save.error.message}</p>}
    </div>}
  </div>;
}
