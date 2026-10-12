import { message } from "../../i18n/formatMessages";
import { t } from "../../i18n/locale";
import { Button, Callout, Tag } from "@blueprintjs/core";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import type { HistorySelection } from "../../api/historyModels";
import { readDecisionDelta, type DecisionDelta, type ResultIdentity } from "../../api/researchFollowup";
import { describeHistoryReasons } from "./historyReasonText";
import { currentnessLabel } from "./historyPresentation";
import { CriteriaChange } from "./CriteriaChange";

const exactDigest = /^[a-f0-9]{64}$/i;

function identity(selection: HistorySelection): ResultIdentity | null {
  if (selection.kind !== "result" || !exactDigest.test(selection.scope.requestDigest) ||
      !selection.resultDigest || !exactDigest.test(selection.resultDigest)) return null;
  return { request_revision_digest: selection.scope.requestDigest, result_revision_digest: selection.resultDigest };
}

function groupLabel(kind: DecisionDelta["groups"][number]["kind"]): string {
  return { get CONTENT() { return t("delta.answerContent"); }, get EVIDENCE() { return t("history.evidence"); }, get CONDITION() { return t("delta.applicability"); }, get STATUS() { return t("delta.state"); }, get ACTION() { return t("delta.nextAction"); }, get OTHER() { return t("delta.other"); } }[kind];
}

function changeLabel(path: string): string {
  const labels: Record<string, string> = {
    get "/result/answer"() { return t("history.answer"); }, get "/phase"() { return t("delta.stage"); }, get "/completion"() { return t("delta.completion"); },
    get "/terminal_reason"() { return t("delta.termination"); }, get "/gaps"() { return t("delta.gaps"); }, get "/next_steps"() { return t("delta.nextSteps"); },
  };
  return labels[path] ?? path.split("/").filter(Boolean).at(-1)?.replaceAll("_", " ") ?? t("delta.changedItem");
}

const previewLimit = 5;
const valuePreviewLimit = 240;
const detailPageSize = 20;
const technicalPath = /(?:^|\/)(?:[^/]*(?:digest|sha256|checksum|hash|_id|_ref)|id|ref|schema_version|record_refs|metadata|trace_paths)(?:\/|$)/i;
const technicalValue = /[a-f0-9]{64}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/i;

function shortText(value: string): string {
  return value.length > valuePreviewLimit ? `${value.slice(0, valuePreviewLimit)}…` : value;
}

function previewValue(value: unknown, depth = 0): string {
  if (value === null || value === undefined) return t("delta.noValue");
  if (typeof value === "string") return technicalValue.test(value) ? t("delta.technicalValue") : shortText(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return depth >= 2 ? message("deltaList", value.length)
    : shortText(message("deltaListPreview", value.length, value.length ? previewValue(value[0], depth + 1) : null));
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (depth >= 2) return message("deltaFields", entries.length);
    const readable = entries.filter(([name]) => !technicalPath.test(`/${name}`));
    const representative = ["answer", "summary", "statement", "status", "reason"].map(key => readable.find(([name]) => name === key)).find(Boolean) ?? readable[0];
    return representative ? shortText(message("deltaFieldPreview", entries.length, representative[0], previewValue(representative[1], depth + 1)))
      : message("deltaOpenFields", entries.length);
  }
  return t("delta.unrenderable");
}

function DecisionValue({ value }: { value: unknown }) {
  const [open, setOpen] = useState(false);
  const expandable = (typeof value === "string" && (value.length > valuePreviewLimit || technicalValue.test(value))) || (typeof value === "object" && value !== null);
  return <div className="decision-value">
    <span>{previewValue(value)}</span>
    {expandable && <Button small minimal aria-expanded={open} onClick={() => setOpen(current => !current)}>{open ? t("delta.closeValue") : t("delta.openValue")}</Button>}
    {open && <pre className="decision-value-full">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>}
  </div>;
}

function previewSelection(groups: DecisionDelta["groups"]): Set<string> {
  const selected = new Set<string>();
  const candidates = groups.map((group, groupIndex) => group.kind === "OTHER" ? []
    : group.changes.map((change, index) => technicalPath.test(change.path) ? null : `${groupIndex}:${index}`).filter((value): value is string => value !== null));
  for (let round = 0; selected.size < previewLimit; round += 1) {
    let added = false;
    for (const entries of candidates) {
      if (entries[round]) {
        selected.add(entries[round]);
        added = true;
        if (selected.size === previewLimit) break;
      }
    }
    if (!added) break;
  }
  return selected;
}

function DecisionGroup({ group, groupIndex, preview }: { group: DecisionDelta["groups"][number]; groupIndex: number; preview: Set<string> }) {
  const [open, setOpen] = useState(false);
  const [limit, setLimit] = useState(detailPageSize);
  const previewIndexes = group.changes.map((_, index) => index).filter(index => preview.has(`${groupIndex}:${index}`));
  const shownIndexes = open ? [...new Set([...previewIndexes, ...group.changes.slice(0, limit).map((_, index) => index)])].sort((a, b) => a - b) : previewIndexes;
  const remaining = group.changes.length - shownIndexes.length;
  return <section className="decision-delta-group">
    <div className="section-title-row"><h4>{groupLabel(group.kind)} <span className="muted">{message("deltaCount", group.changes.length)}</span></h4>
      {group.changes.length > previewIndexes.length && <Button small minimal aria-expanded={open} onClick={() => setOpen(current => !current)}>
        {open ? t("delta.closeDetails") : message("deltaViewDetails", group.changes.length - previewIndexes.length)}
      </Button>}</div>
    {shownIndexes.map(index => {
      const change = group.changes[index];
      return <article className="decision-delta-change" key={`${change.path}-${index}`}>
        <h5>{changeLabel(change.path)}</h5>
        <div className="decision-delta-values">
          <div><strong>{t("delta.before")}</strong>{change.before_present ? <DecisionValue value={change.before} /> : <span>{t("delta.noRecord")}</span>}</div>
          <div><strong>{t("delta.after")}</strong>{change.after_present ? <DecisionValue value={change.after} /> : <span>{t("delta.noRecord")}</span>}</div>
        </div>
      </article>;
    })}
    {remaining > 0 && <p className="muted">{message("deltaRemaining", remaining)}</p>}
    {open && limit < group.changes.length && <Button small onClick={() => setLimit(current => current + detailPageSize)}>{t("delta.nextPage")}</Button>}
  </section>;
}

export function DecisionDeltaView({ delta }: { delta: DecisionDelta }) {
  const [reasonDetailsOpen, setReasonDetailsOpen] = useState(false);
  const totalChanges = delta.groups.reduce((total, group) => total + group.changes.length, 0);
  const preview = previewSelection(delta.groups);
  const byCriterion = delta.criteria_state === "AVAILABLE";
  // A plain function, not a component: its children keep their state when this view re-renders.
  const fold = (children: ReactNode) => byCriterion
    ? <details className="decision-delta-details"><summary>{t("delta.details")}</summary>{children}</details> : <>{children}</>;
  return <section className="decision-delta-view" aria-label={t("delta.selectedComparison")}>
    {delta.state !== "UNAVAILABLE" && <CriteriaChange delta={delta}/>}
    {fold(<>
    <div className="section-title-row"><h3>{t("delta.allChanges")}</h3><Tag minimal intent={delta.state === "UNAVAILABLE" ? "warning" : "none"}>
      {delta.state === "CHANGED" ? t("delta.changed") : delta.state === "NO_CHANGE" ? t("delta.unchanged") : delta.state === "PARTIAL" ? t("delta.partial") : t("followup.unverifiable")}
    </Tag></div>
    {delta.state === "NO_CHANGE" && <p>{t("delta.noChanges")}</p>}
    {delta.state === "PARTIAL" && <Callout compact intent="warning">{t("delta.partialNotice")}</Callout>}
    {delta.state === "UNAVAILABLE" && <Callout compact intent="warning">{t("delta.unavailable")}</Callout>}
    {delta.state !== "UNAVAILABLE" && totalChanges > 0 && <p className="decision-delta-count">{message("deltaPreviewCount", totalChanges, preview.size)}</p>}
    {delta.state !== "UNAVAILABLE" && delta.groups.map((group, index) => <DecisionGroup key={`${group.kind}-${index}`} group={group} groupIndex={index} preview={preview} />)}
    {delta.state === "CHANGED" && delta.groups.length === 0 && <p>{t("delta.noDetails")}</p>}
    </>)}
    <div className="decision-delta-reasons"><h4>{t("delta.reasons")}</h4>
      {delta.reason_state === "UNKNOWN_REASON" ? <p>{t("delta.reasonUnknown")}</p>
        : <>{delta.reason_codes.length ? <ul>{describeHistoryReasons(delta.reason_codes).slice(0, 3).map(line => <li key={line}>{line}</li>)}</ul> : <p>{t("delta.noReasonText")}</p>}
          {(describeHistoryReasons(delta.reason_codes).length > 3 || delta.reason_refs.length > 0) && <Button small minimal aria-expanded={reasonDetailsOpen} onClick={() => setReasonDetailsOpen(value => !value)}>
            {reasonDetailsOpen ? t("delta.closeReasons") : message("deltaReasonDetails", delta.reason_refs.length)}
          </Button>}
          {reasonDetailsOpen && describeHistoryReasons(delta.reason_codes).length > 3 && <ul>{describeHistoryReasons(delta.reason_codes).slice(3).map(line => <li key={line}>{line}</li>)}</ul>}
          {reasonDetailsOpen && delta.reason_refs.length > 0 && <p>{t("labels.linkedRecords")}{" "}{delta.reason_refs.map((ref, index) => {
            const kind = typeof ref.entity_type === "string" ? ref.entity_type : t("delta.record");
            const id = typeof ref.entity_id === "string" ? ref.entity_id : t("delta.noIdentifier");
            return <span key={index}>{index > 0 ? " · " : ""}{kind} {id}</span>;
          })}</p>}
          {delta.reason_codes.length > 0 && <details className="connection-tech"><summary>{t("oauth.technical")}</summary>{delta.reason_codes.map((code, index) => <small key={`${code}-${index}`}>{code}</small>)}</details>}</>}
    </div>
    <p className="muted">{message("deltaCurrentness", currentnessLabel(delta.basis_currentness.before ?? {state:"UNKNOWN_BASIS",reasons:[]}), currentnessLabel(delta.basis_currentness.after ?? {state:"UNKNOWN_BASIS",reasons:[]}))}</p>
  </section>;
}

export function DecisionDeltaComparison({ before, after, actorScope, historyCheckEpoch = 0, suspended = false }: {
  before: HistorySelection; after: HistorySelection; actorScope: string | null; historyCheckEpoch?: number; suspended?: boolean;
}) {
  const beforeId = identity(before);
  const afterId = identity(after);
  const sameScope = before.scope.projectId === after.scope.projectId && before.scope.threadId === after.scope.threadId;
  const distinct = Boolean(beforeId && afterId && (beforeId.request_revision_digest !== afterId.request_revision_digest || beforeId.result_revision_digest !== afterId.result_revision_digest));
  const ready = Boolean(actorScope && beforeId && afterId && sameScope && distinct);
  const comparison = useQuery({
    queryKey: ["decision-delta", actorScope, before.scope.projectId, before.scope.threadId,
      beforeId?.request_revision_digest, beforeId?.result_revision_digest, afterId?.request_revision_digest, afterId?.result_revision_digest, historyCheckEpoch],
    enabled: ready, retry: false, gcTime: 1000,
    // Keep this pure read alive across StrictMode's transient unsubscribe. The exact
    // actor/identity key fences late data, while rpc() still enforces its 15s timeout.
    queryFn: () => readDecisionDelta(before.scope.projectId, before.scope.threadId!, beforeId!, afterId!),
  });
  if (!beforeId || !afterId) return <p role="status">{t("delta.noIdentity")}</p>;
  if (!sameScope) return <p role="status">{t("delta.sameScope")}</p>;
  if (!distinct) return <p role="status">{t("delta.distinct")}</p>;
  if (!actorScope) return <p role="status">{t("delta.checkAccess")}</p>;
  if (suspended) return <p role="status">{t("delta.checkingAccess")}</p>;
  if (comparison.isPending || comparison.isFetching) return <p role="status">{t("delta.comparing")}</p>;
  if (comparison.error) return <Callout intent="warning" role="alert">{t("delta.failed")}<Button small onClick={() => void comparison.refetch()}>{t("delta.retry")}</Button></Callout>;
  return <DecisionDeltaView delta={comparison.data} />;
}
