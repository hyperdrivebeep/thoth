import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Icon, Spinner } from "@blueprintjs/core";

import { liveResearchProgress } from "../api/researchProgress";
import type { ResearchStatus } from "../api/research";
import { Disclosure } from "./Disclosure";
import { ResearchActivityStrip } from "./ResearchActivityStrip";

type LiveProgress = NonNullable<ReturnType<typeof liveResearchProgress>>;

function evidenceScopeLine(progress: LiveProgress): string | null {
  const shown = progress.evidenceLabels.length;
  // Several ranges of one source can carry the same place label ("1행"); a place is named once.
  const places = [...new Set(progress.evidenceLabels)];
  const coverage = progress.evidenceTotal != null && progress.evidenceTotal > shown
    ? formatMessage("progressLocationCoverage", shown, progress.evidenceTotal) : null;
  if (shown > 0) {
    const named = places.slice(0, 2);
    const remaining = shown > named.length ? formatMessage("moreLocations", shown - named.length) : "";
    return [named.join(" · ") + remaining, coverage].filter(Boolean).join(" · ");
  }
  if (progress.evidenceTotal === 0) return t("liveProgress.noCandidates");
  return coverage ? `${formatMessage("progressLocationUnavailable")} · ${coverage}` : null;
}

function judgementLine(progress: LiveProgress): string | null | undefined {
  const state = progress.hypothesisCount==null ? progress.missing
    : formatMessage("hypothesesReviewed", progress.hypothesisCount, progress.reviewCount??0);
  // The reason line often ends with the very sentence the state line would say; it is said once.
  const judgement = state && progress.current.detail.includes(state) ? null : state;
  return judgement;
}

export function ResearchLiveProgress({
  status,
  sourceUris = [],
}: {
  status?: ResearchStatus;
  sourceUris?: string[];
}) {
  const progress = liveResearchProgress(status, sourceUris);
  if (!progress) {
    return null;
  }
  const notes = progress.events.filter(event => event.kind === "note" && !event.live).map(event=>event.text);
  const contextNotes = [...new Set([notes[0],notes.at(-1)])].filter((note):note is string=>Boolean(note)&&note!==progress.current.title);
  const evidenceScope = evidenceScopeLine(progress);
  const judgement = judgementLine(progress);
  return (
    <aside className="research-progress" aria-label={t("liveProgress.title")}>
      <div className="research-progress-summary" role="status" aria-live="polite" aria-atomic="true">
        {status?.operation_state === "RUNNING" ? <Spinner size={18}/> : <Icon icon="tick-circle" size={18}/>}<div><span>{t("liveProgress.now")}</span><strong>{progress.current.title}</strong></div>
      </div>
      <div className="research-progress-brief">
        <div><span>{t("liveProgress.why")}</span><p>{progress.current.detail}</p></div>
        {contextNotes.length>0&&<div><span>{t("liveProgress.checked")}</span><p>{contextNotes.join(" · ")}</p></div>}
        {evidenceScope&&<div><span>{t("liveProgress.scope")}</span><p>{evidenceScope}</p></div>}
        {judgement&&<div><span>{t("history.assessmentState")}</span><p>{judgement}</p></div>}
      </div>
      <ResearchActivityStrip events={status?.user_activity_events} />
      <Disclosure label={t("liveProgress.details")}>
        <ol className="research-progress-timeline">
          {progress.events.map((event) =>
            event.kind === "note" ? (
              <li key={event.id} className={event.live ? "research-progress-note live" : "research-progress-note"}>
                {event.text}
              </li>
            ) : (
              <li key={event.id} className={event.live ? "research-progress-action live" : "research-progress-action"}>
                {event.live ? <Spinner size={16} /> : <Icon icon={event.icon ?? "tick-circle"} size={14} />}
                <span className="research-progress-action-label">{event.label}</span>
                {event.payload ? <code>{event.payload}</code> : null}
              </li>
            ),
          )}
        </ol>
        {(progress.modelLine || progress.usageLine) && (
          <p className="research-progress-model">
            {[progress.modelLine, progress.usageLine].filter(Boolean).join(" · ")}
          </p>
        )}
      </Disclosure>
    </aside>
  );
}
