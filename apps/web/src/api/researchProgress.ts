import { t } from "../i18n/locale";
import { message as formatMessage } from "../i18n/formatMessages";
import { getLocale } from "../i18n/locale";
import type { ResearchStatus } from "./research";
import { sourceCardTitle } from "../sourceDisplay";
import { progressEvidenceLabels, progressSourceActionLocation } from "./progressEvidenceLocations";
import {
  formatDuration,
  ACTION_COPY,
  clip,
  compactPayload,
  formatBytes,
  hypothesisActionPayload,
  missingFor,
  modelActionPayload,
  sceneFor,
  stageActionPayload,
  unique,
  usageLine,
  waitLine,
  type ProgressScene,
  type TimelineIcon,
  type WorkFacts,
} from "./researchProgressText";

export { formatDuration };
export type { ProgressScene, TimelineIcon };

export type TimelineEvent = {
  id: string;
  kind: "note" | "action";
  text: string;
  live?: boolean;
  actionType?: "stage_completed" | "source_read" | "source_attached" | "model_call" | "hypothesis_review";
  label?: string;
  payload?: string | null;
  icon?: TimelineIcon;
};

export type LiveResearchProgress = {
  phase: string;
  headline: string;
  current: ProgressScene;
  missing: string | null;
  events: TimelineEvent[];
  evidenceLabels: string[];
  evidenceTotal: number | null;
  hypothesisCount: number | null;
  reviewCount: number | null;
  modelLine: string | null;
  waitLine: string | null;
  usageLine: string | null;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function questionText(status?: ResearchStatus): string {
  return [status?.request?.authored_text, status?.request?.effective_question, status?.problem]
    .filter((value): value is string => typeof value === "string" && value.trim().length > 1 && value.trim() !== "q")
    .join("\n");
}

function extractFocus(status?: ResearchStatus): string | null {
  const problem = typeof status?.problem === "string" ? status.problem.trim() : "";
  if (problem.length > 1 && problem !== "q") {
    return clip(problem.replace(/[.?。]+$/u, ""), 42);
  }
  const text = questionText(status);
  const table = text.match(/표\s*\d+\s*[^,.\n]{0,30}조건(?:\s*\d+개)?/u);
  if (table) {
    return clip(table[0], 42);
  }
  const first = text.split(/[,.\n]/u)[0]?.trim() ?? "";
  return first.length > 2 ? clip(first, 42) : null;
}

function extractConstraints(status?: ResearchStatus): string[] {
  const text = questionText(status);
  const found: string[] = [];
  const table = text.match(/표\s*\d+\s*[^,.\n]{0,30}조건(?:\s*\d+개)?/u);
  if (table) {
    found.push(table[0].replace(/\s+/g, " ").trim());
  }
  if (/표 숫자만/u.test(text)) {
    found.push(t("progress.tableOnly"));
  }
  if (/추측 금지/u.test(text)) {
    found.push(t("progress.noGuess"));
  }
  if (/원문 위치/u.test(text)) {
    found.push(t("progress.withLocator"));
  }
  if (/확인 안 되면|부족한지|HOLD/u.test(text)) {
    found.push(t("progress.explainMissing"));
  }
  const quotes = [...text.matchAll(/[“「"]([^”」"]{2,48})[”」"]/gu)].map((match) => match[1].trim());
  return unique([...found, ...quotes]);
}

function hypothesisRecords(status?: ResearchStatus): Array<{ id: string; statement: string }> {
  const portfolio = asRecord(asRecord(status?.attempt?.draft_progress)?.portfolio);
  return asList(portfolio?.hypotheses)
    .map((item, index) => {
      const record = asRecord(item);
      const id =
        typeof record?.hypothesis_id === "string" && record.hypothesis_id
          ? record.hypothesis_id
          : `h${index + 1}`;
      const statement = record?.statement ?? record?.text ?? record?.label;
      return {
        id,
        statement: typeof statement === "string" ? statement.trim() : "",
      };
    })
    .filter((item) => item.statement.length > 0);
}

function decidedHypothesisIds(status?: ResearchStatus): Set<string> {
  const review = asRecord(asRecord(status?.attempt?.draft_progress)?.hypothesis_review);
  return new Set(
    asList(review?.decisions)
      .map((item) => asRecord(item)?.hypothesis_id)
      .filter((id): id is string => typeof id === "string" && id.length > 0),
  );
}

function workFacts(status: ResearchStatus, sourceUris: string[] = []): WorkFacts {
  const records = hypothesisRecords(status);
  const decidedIds = decidedHypothesisIds(status);
  const quotes = [...questionText(status).matchAll(/[“「]([^”」]{2,48})[”」]/gu)].map((match) => match[1].trim());
  const hypotheses = unique([...records.map((item) => item.statement), ...quotes]);
  const decided = records.filter((item) => decidedIds.has(item.id)).map((item) => item.statement);
  const pending = records.filter((item) => !decidedIds.has(item.id)).map((item) => item.statement);
  return {
    focus: extractFocus(status),
    constraints: extractConstraints(status),
    locators: evidenceLabels(status).slice(0, 2),
    hypotheses,
    pendingHypotheses: unique(pending),
    decidedHypotheses: unique(decided),
    sources: unique(sourceUris.slice(0, 8).map((uri) => sourceCardTitle(uri))),
  };
}

export function evidenceLabels(status?: ResearchStatus): string[] {
  return progressEvidenceLabels(status);
}

function evidenceTotal(status?: ResearchStatus): number | null {
  const focus = asRecord(asRecord(status?.attempt?.draft_progress)?.evidence_focus);
  const total = numberOrNull(focus?.total);
  return total !== null && Number.isSafeInteger(total) && total >= 0 ? total : null;
}

function hypothesisCount(status?: ResearchStatus): number | null {
  const draft = asRecord(status?.attempt?.draft_progress);
  const portfolio = asRecord(draft?.portfolio);
  const hypotheses = asList(portfolio?.hypotheses);
  if (hypotheses.length > 0) {
    return hypotheses.length;
  }
  return null;
}

function reviewCount(status?: ResearchStatus): number | null {
  const draft = asRecord(status?.attempt?.draft_progress);
  const review = asRecord(draft?.hypothesis_review);
  const decisions = asList(review?.decisions);
  return decisions.length > 0 ? decisions.length : null;
}

function modelLine(status?: ResearchStatus): string | null {
  const settings = asRecord(status?.request?.model_settings);
  if (!settings) {
    return null;
  }
  const model = typeof settings.model === "string" && settings.model ? settings.model : null;
  const effort =
    typeof settings.reasoning_effort === "string" && settings.reasoning_effort
      ? settings.reasoning_effort
      : null;
  if (!model && !effort) {
    return null;
  }
  if (model && effort) {
    return formatMessage("modelReasoning", model, effort);
  }
  return model ?? (effort ? formatMessage("reasoning", effort) : null);
}

function actionEvent(
  id: string,
  actionType: NonNullable<TimelineEvent["actionType"]>,
  payload: string | null,
  live = false,
): TimelineEvent {
  const copy = ACTION_COPY[actionType];
  return {
    id,
    kind: "action",
    text: copy.label,
    live,
    actionType,
    label: copy.label,
    payload,
    icon: copy.icon,
  };
}

function timelineFromStages(status: ResearchStatus, sourceUris: string[], facts: WorkFacts): TimelineEvent[] {
  const events: TimelineEvent[] = [];
  const stages = status.completed_stages ?? [];
  stages.forEach((stage, index) => {
    const role = typeof stage.role === "string" && stage.role ? stage.role : "UNKNOWN";
    const scene = sceneFor(role, facts);
    events.push({
      id: `stage-note-${index}`,
      kind: "note",
      text: scene.title,
    });
    events.push(actionEvent(`stage-action-${index}`, "stage_completed", stageActionPayload(stage)));
  });
  sourceUris.slice(0, 8).forEach((uri, index) => {
    events.push(actionEvent(`source-${index}`, "source_attached", sourceCardTitle(uri)));
  });
  const labels = evidenceLabels(status);
  labels.forEach((label, index) => {
    events.push(actionEvent(`locator-${index}`, "source_read", label));
  });
  return events;
}

function timelineFromPublished(status: ResearchStatus, sourceUris: string[], facts: WorkFacts): TimelineEvent[] | null {
  const published = status.activity_events;
  if (!Array.isArray(published) || published.length === 0) {
    return null;
  }
  const events: TimelineEvent[] = [];
  let insertedSources = false;
  let sourceReadIndex = 0;
  published.forEach((event, index) => {
    const live = event.live === true;
    if (live && !insertedSources) {
      sourceUris.slice(0, 8).forEach((uri, sourceIndex) => {
        events.push(actionEvent(`source-${sourceIndex}`, "source_attached", sourceCardTitle(uri)));
      });
      insertedSources = true;
    }
    if (event.kind === "note") {
      const key = typeof event.key === "string" ? event.key : undefined;
      const scene = sceneFor(key, facts, live);
      events.push({
        id: `event-note-${event.seq ?? index}`,
        kind: "note",
        text: scene.title,
        live,
      });
      return;
    }
    const actionType = event.action_type;
    const payload = event.payload ?? {};
    if (actionType === "stage_completed") {
      events.push(
        actionEvent(
          `event-action-${event.seq ?? index}`,
          "stage_completed",
          compactPayload([
            formatDuration(numberOrNull(payload.elapsed_ms)),
            formatBytes(numberOrNull(payload.context_bytes)),
            typeof payload.dispatch_count === "number" && payload.dispatch_count > 0
              ? formatMessage("modelCalls", payload.dispatch_count)
              : null,
          ]),
          live,
        ),
      );
      return;
    }
    if (actionType === "source_read") {
      events.push(
        actionEvent(
          `event-action-${event.seq ?? index}`,
          "source_read",
          progressSourceActionLocation(status, payload, sourceReadIndex++),
          live,
        ),
      );
      return;
    }
    if (actionType === "hypothesis_review") {
      events.push(
        actionEvent(
          `event-action-${event.seq ?? index}`,
          "hypothesis_review",
          hypothesisActionPayload(
            numberOrNull(payload.hypothesis_count),
            numberOrNull(payload.review_count),
          ),
          live,
        ),
      );
      return;
    }
    if (actionType === "model_call") {
      const elapsed = formatDuration(numberOrNull(payload.elapsed_ms));
      const firstByte = formatDuration(numberOrNull(payload.first_byte_ms));
      const received = numberOrNull(payload.received_bytes);
      const waiting = payload.state === "RESERVED" || live;
      events.push(
        actionEvent(
          `event-action-${event.seq ?? index}`,
          "model_call",
          waiting
            ? compactPayload([elapsed ?? t("progress.elapsedUnknown"), firstByte ? formatMessage("firstByte", firstByte) : t("progress.noFirstByte")])
            : compactPayload([received != null ? `${received.toLocaleString(getLocale() === "en" ? "en-US" : "ko-KR")}B` : null, elapsed]),
          waiting,
        ),
      );
    }
  });
  if (!insertedSources) {
    sourceUris.slice(0, 8).forEach((uri, sourceIndex) => {
      events.push(actionEvent(`source-${sourceIndex}`, "source_attached", sourceCardTitle(uri)));
    });
  }
  return events;
}

function withLiveNote(events: TimelineEvent[], text: string): TimelineEvent[] {
  const lastLive = [...events].reverse().find((event) => event.kind === "note" && event.live);
  if (lastLive) {
    return events.map((event) => (event.id === lastLive.id ? { ...event, text } : event));
  }
  return [...events, { id: "live-note", kind: "note", text, live: true }];
}

function ensureLiveModel(events: TimelineEvent[], status: ResearchStatus): TimelineEvent[] {
  if (events.some((event) => event.actionType === "model_call")) {
    return events;
  }
  const model = modelActionPayload(status);
  if (!model) {
    return events;
  }
  return [...events, actionEvent("live-model", "model_call", model.payload, model.live)];
}

function ensureHypothesis(
  events: TimelineEvent[],
  hypotheses: number | null,
  reviews: number | null,
): TimelineEvent[] {
  if (events.some((event) => event.actionType === "hypothesis_review") || hypotheses == null) {
    return events;
  }
  return [
    ...events,
    actionEvent(
      "live-hypothesis",
      "hypothesis_review",
      hypothesisActionPayload(hypotheses, reviews),
      reviews == null || reviews < hypotheses,
    ),
  ];
}

export function liveResearchProgress(
  status?: ResearchStatus,
  sourceUris: string[] = [],
): LiveResearchProgress | null {
  if (!status?.attempt) {
    return null;
  }
  const running = status.operation_state === "RUNNING";
  const phase = status.attempt.phase ?? "RUNNING";
  const labels = evidenceLabels(status);
  const total = evidenceTotal(status);
  const hypotheses = hypothesisCount(status);
  const reviews = reviewCount(status);
  const facts = workFacts(status, sourceUris);
  const current = sceneFor(phase, facts, running);
  const missing = phase === "EVIDENCE_FOCUS" && labels.length === 0 && total === null
    ? formatMessage("progressLocationUnavailable")
    : missingFor(status, facts, labels, total, hypotheses, reviews);
  const liveText = missing ? `${current.title} ${missing}` : current.title;
  let events =
    timelineFromPublished(status, sourceUris, facts) ?? timelineFromStages(status, sourceUris, facts);
  if (!running) {
    events = events.map((event) => (event.live ? { ...event, live: false } : event));
  }
  events = withLiveNote(events, liveText);
  events = ensureHypothesis(events, hypotheses, reviews);
  events = ensureLiveModel(events, status);
  if (!running) {
    events = events.map((event) => (event.live ? { ...event, live: false } : event));
  }
  return {
    phase,
    headline: current.title,
    current: missing ? { title: current.title, detail: `${current.detail} ${missing}` } : current,
    missing,
    events,
    evidenceLabels: labels,
    evidenceTotal: total,
    hypothesisCount: hypotheses,
    reviewCount: reviews,
    modelLine: modelLine(status),
    waitLine: waitLine(status),
    usageLine: usageLine(status),
  };
}

export function composerActivity(status: ResearchStatus): string | null {
  if (status.execution_state === "PAUSE_PENDING") {
    return t("progress.pausing");
  }
  if (status.execution_state === "PAUSED") {
    return t("progress.paused");
  }
  if (status.attempt?.remote_observation === "UNKNOWN" && status.attempt.external_effect_state === "DISPATCHING") {
    return t("progress.externalUnknown");
  }
  if (status.operation_state !== "RUNNING") {
    return null;
  }
  const live = liveResearchProgress(status);
  if (!live) {
    return null;
  }
  const wait = live.waitLine ? ` · ${live.waitLine}` : "";
  return `${live.headline}${wait}`;
}
