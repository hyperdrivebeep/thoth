import { message as formatMessage } from "../i18n/formatMessages";
import { activityAction, activityDisplay } from "../i18n/activityDisplay";
import { t, getLocale } from "../i18n/locale";
import { Icon, Spinner, Tag } from "@blueprintjs/core";

import type { UserActivityEvent, UserActivityLocator } from "../api/research";
import { Disclosure } from "./Disclosure";

const stateLabels: Record<UserActivityEvent["state"], string> = {
  get planned() { return t("activity.planned"); },
  get running() { return t("activity.running"); },
  get succeeded() { return t("activity.succeeded"); },
  get failed() { return t("activity.failed"); },
  get blocked() { return t("activity.blocked"); },
  get cancel_requested() { return t("activity.cancelRequested"); },
  get cancelled() { return t("activity.cancelled"); },
  get timed_out() { return t("activity.timeout"); },
  get unknown_external_effect() { return t("activity.externalUnknown"); },
};

const relationLabels: Record<UserActivityEvent["research_relation"], string> = {
  get none() { return t("activity.noRelation"); },
  get discovered() { return t("activity.discovered"); },
  get opened() { return t("activity.opened"); },
  get fetched() { return t("activity.fetched"); },
  get parsed() { return t("activity.parsed"); },
  get read() { return t("activity.read"); },
  get screened() { return t("activity.screened"); },
  get selected_candidate() { return t("activity.candidate"); },
  get supports() { return t("activity.supports"); },
  get contradicts() { return t("activity.contradicts"); },
  get inconclusive() { return t("activity.inconclusive"); },
  get held() { return t("activity.held"); },
  get excluded() { return t("activity.excluded"); },
  get stale() { return t("activity.stale"); },
  get access_blocked() { return t("activity.accessBlocked"); },
};

const currentnessLabels = {
  get current() { return t("activity.currentSource"); },
  get stale() { return t("activity.changedSource"); },
  get unknown_time() { return t("activity.unknownSourceTime"); },
  get access_unverified() { return t("activity.unknownAccess"); },
} as const;

function formatDuration(milliseconds: number) {
  if (milliseconds < 1000) return `${milliseconds}ms`;
  if (milliseconds < 60_000) return formatMessage("seconds", (milliseconds / 1000).toFixed(milliseconds % 1000 === 0 ? 0 : 1));
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.round((milliseconds % 60_000) / 1000);
  return seconds > 0 ? formatMessage("minutesSeconds", minutes, seconds) : formatMessage("minutes", minutes);
}

function formatLocator(locator?: UserActivityLocator | null) {
  if (!locator) return null;
  return [
    locator.page ? formatMessage("page", locator.page) : null,
    locator.section ? formatMessage("section", locator.section) : null,
    locator.cell ? formatMessage("cell", locator.cell) : null,
    locator.line_start ? formatMessage("lines", locator.line_start, locator.line_end) : null,
  ].filter(Boolean).join(" · ") || null;
}

function stateIcon(event: UserActivityEvent) {
  if (event.state === "running") return <Spinner size={14} />;
  if (event.state === "failed" || event.state === "blocked" || event.state === "timed_out") return <Icon icon="warning-sign" size={14} />;
  if (event.state === "cancelled" || event.state === "cancel_requested") return <Icon icon="disable" size={14} />;
  if (event.state === "succeeded") return <Icon icon="tick-circle" size={14} />;
  return <Icon icon="time" size={14} />;
}

function targetLabel(event: UserActivityEvent) {
  const target = event.target;
  if (!target) return null;
  return [targetTitle(event), target.display_ref, formatLocator(target.locator)].filter(Boolean).join(" · ");
}

function targetTitle(event: UserActivityEvent) {
  const target = event.target;
  // The server redacts span titles to this fixed placeholder, not an authored title.
  if (target?.kind === "span" && target.title === "연결된 자료") return t("activity.sourceFallback");
  return target?.kind === "model" && target.title === "모델 검토기" ? t("activity.modelReviewer") : target?.title;
}

function toolTitle(tool: NonNullable<UserActivityEvent["tool"]>) {
  if (tool.family === "MODEL" && tool.display_name === "모델 검토기") return t("activity.modelReviewer");
  if (tool.family === "LOCAL" && tool.operation === "READ" && tool.display_name === "프로젝트 자료") return t("activity.projectSource");
  return tool.display_name;
}

function SafeArguments({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value);
  if (entries.length === 0) return <span className="activity-empty">{t("activity.noArgs")}</span>;
  return <dl className="activity-arguments">
    {entries.map(([key, item]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{typeof item === "string" ? item : JSON.stringify(item)}</dd></div>)}
  </dl>;
}

function ActivityDetails({ event }: { event: UserActivityEvent }) {
  const target = event.target;
  const locator = formatLocator(target?.locator);
  const resultParts = [
    event.result?.duration_ms != null ? formatMessage("duration", formatDuration(event.result.duration_ms)) : null,
    event.result?.http_status != null ? `HTTP ${event.result.http_status}` : null,
    event.result?.exit_code != null ? formatMessage("exitCode", event.result.exit_code) : null,
  ].filter(Boolean);
  return <div className="research-activity-detail">
    {getLocale() === "en" && <details className="connection-tech"><summary>Original activity detail</summary><p>{event.label_ko}</p>{event.why_ko && <p>{event.why_ko}</p>}{event.result?.summary_ko && <p>{event.result.summary_ko}</p>}</details>}
    {event.why_ko && <p><strong>{t("traceHistory.reason")}</strong>{activityDisplay(event.why_ko)}</p>}
    {event.tool && <section aria-label={t("activity.toolInfo")}>
      <h5>{t("activity.tool")}</h5>
      <p>{toolTitle(event.tool)} · {event.tool.family} · {event.tool.operation}</p>
      <SafeArguments value={event.tool.sanitized_args} />
      {!event.tool.raw_command_available && <p className="activity-limit">{t("activity.noRawCommand")}</p>}
    </section>}
    {target && <section aria-label={t("activity.location")}>
      <h5>{t("activity.location")}</h5>
      <p>{targetTitle(event)}</p>
      {[target.display_ref, target.host_alias, target.safe_uri, locator, target.source_version_id ? formatMessage("version", target.source_version_id) : null, target.hash_short ? formatMessage("hash", target.hash_short) : null].filter((item): item is string => Boolean(item)).map((item) => <code key={item}>{item}</code>)}
      <p>{target.currentness ? currentnessLabels[target.currentness] : t("activity.noCurrentness")} {formatMessage("accessState", target.access_state)}</p>
    </section>}
    {event.result && <section aria-label={t("result.outcome")}>
      <h5>{t("result.outcome")}</h5>
      {event.result.summary_ko && <p>{activityDisplay(event.result.summary_ko)}</p>}
      {resultParts.length > 0 && <p>{resultParts.join(" · ")}</p>}
      {event.result.counts && Object.keys(event.result.counts).length > 0 && <SafeArguments value={event.result.counts} />}
      {event.result.reason_code && <code>{event.result.reason_code}</code>}
    </section>}
    {event.redaction?.applied && <section aria-label={t("activity.protected")}>
      <h5>{t("activity.protected")}</h5>
      <p>{activityDisplay(event.redaction.public_note_ko) ?? t("activity.redacted")}</p>
      {event.redaction.classes && event.redaction.classes.length > 0 && <p>{event.redaction.classes.join(" · ")}</p>}
    </section>}
  </div>;
}

export function ResearchActivityStrip({ events, detailOpen = false }: { events?: UserActivityEvent[]; detailOpen?: boolean }) {
  if (!events || events.length === 0) return null;
  const defaultEvents = events.filter((event) => event.visibility === "default").slice(-5);
  const summaryEvents = defaultEvents.length > 0 ? defaultEvents : events.slice(-5);
  return <section className="research-activity" aria-label={t("activity.title")}>
    <div className="research-activity-heading">
      <div><span>{t("activity.progressEvidence")}</span><strong>{t("activity.recent")}</strong></div>
      <span>{formatMessage("activityCount", summaryEvents.length)}</span>
    </div>
    <ol className="research-activity-list">
      {summaryEvents.map((event) => <li key={event.event_id} data-severity={event.severity}>
        <div className="research-activity-icon">{stateIcon(event)}</div>
        <div className="research-activity-main">
          <strong>{activityDisplay(event.label_ko, activityAction[event.action] ?? "Research activity")}</strong>
          {targetLabel(event) && <span>{targetLabel(event)}</span>}
          <div className="research-activity-badges">
            <Tag minimal>{stateLabels[event.state]}</Tag>
            {event.research_relation !== "none" && <Tag minimal intent={event.research_relation === "contradicts" || event.research_relation === "stale" ? "warning" : "primary"}>{relationLabels[event.research_relation]}</Tag>}
          </div>
        </div>
      </li>)}
    </ol>
    <Disclosure label={formatMessage("activityDetails", events.length)} defaultOpen={detailOpen}>
      <ol className="research-activity-expanded">
        {events.map((event) => <li key={event.event_id}>
          <header>
            {stateIcon(event)}<strong>{activityDisplay(event.label_ko, activityAction[event.action] ?? "Research activity")}</strong>
            <Tag minimal>{stateLabels[event.state]}</Tag>
            <Tag minimal>{relationLabels[event.research_relation]}</Tag>
          </header>
          <ActivityDetails event={event} />
        </li>)}
      </ol>
    </Disclosure>
  </section>;
}
