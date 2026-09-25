import { Icon, Spinner, Tag } from "@blueprintjs/core";

import type { UserActivityEvent, UserActivityLocator } from "../api/research";
import { Disclosure } from "./Disclosure";

const stateLabels: Record<UserActivityEvent["state"], string> = {
  planned: "예정",
  running: "실행 중",
  succeeded: "실행 완료",
  failed: "실패",
  blocked: "차단됨",
  cancel_requested: "취소 요청됨",
  cancelled: "취소됨",
  timed_out: "시간 초과",
  unknown_external_effect: "외부 결과 확인 필요",
};

const relationLabels: Record<UserActivityEvent["research_relation"], string> = {
  none: "판단 관계 없음",
  discovered: "자료 발견",
  opened: "자료 열람",
  fetched: "자료 가져옴",
  parsed: "내용 구조화",
  read: "원문 확인",
  screened: "관련성 검토",
  selected_candidate: "근거 후보",
  supports: "판단을 지지",
  contradicts: "판단을 반박",
  inconclusive: "판단 보류",
  held: "검토 보류",
  excluded: "근거에서 제외",
  stale: "현재성 만료",
  access_blocked: "접근 차단",
};

const currentnessLabels = {
  current: "현재 자료",
  stale: "기준 시점 이후 변경됨",
  unknown_time: "자료 시점 미확인",
  access_unverified: "접근 범위 미확인",
} as const;

function formatDuration(milliseconds: number) {
  if (milliseconds < 1000) return `${milliseconds}ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(milliseconds % 1000 === 0 ? 0 : 1)}초`;
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.round((milliseconds % 60_000) / 1000);
  return seconds > 0 ? `${minutes}분 ${seconds}초` : `${minutes}분`;
}

function formatLocator(locator?: UserActivityLocator | null) {
  if (!locator) return null;
  return [
    locator.page ? `${locator.page}쪽` : null,
    locator.section ? `절 ${locator.section}` : null,
    locator.cell ? `셀 ${locator.cell}` : null,
    locator.line_start ? `줄 ${locator.line_start}${locator.line_end && locator.line_end !== locator.line_start ? `–${locator.line_end}` : ""}` : null,
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
  return [target.title, target.display_ref, formatLocator(target.locator)].filter(Boolean).join(" · ");
}

function SafeArguments({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value);
  if (entries.length === 0) return <span className="activity-empty">표시할 인자 없음</span>;
  return <dl className="activity-arguments">
    {entries.map(([key, item]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{typeof item === "string" ? item : JSON.stringify(item)}</dd></div>)}
  </dl>;
}

function ActivityDetails({ event }: { event: UserActivityEvent }) {
  const target = event.target;
  const locator = formatLocator(target?.locator);
  const resultParts = [
    event.result?.duration_ms != null ? `소요 ${formatDuration(event.result.duration_ms)}` : null,
    event.result?.http_status != null ? `HTTP ${event.result.http_status}` : null,
    event.result?.exit_code != null ? `종료 코드 ${event.result.exit_code}` : null,
  ].filter(Boolean);
  return <div className="research-activity-detail">
    {event.why_ko && <p><strong>이유</strong>{event.why_ko}</p>}
    {event.tool && <section aria-label="도구 정보">
      <h5>도구</h5>
      <p>{event.tool.display_name} · {event.tool.family} · {event.tool.operation}</p>
      <SafeArguments value={event.tool.sanitized_args} />
      {!event.tool.raw_command_available && <p className="activity-limit">원시 명령은 이 화면에 제공되지 않습니다.</p>}
    </section>}
    {target && <section aria-label="자료 위치">
      <h5>자료 위치</h5>
      <p>{target.title}</p>
      {[target.display_ref, target.host_alias, target.safe_uri, locator, target.source_version_id ? `버전 ${target.source_version_id}` : null, target.hash_short ? `해시 ${target.hash_short}` : null].filter((item): item is string => Boolean(item)).map((item) => <code key={item}>{item}</code>)}
      <p>{target.currentness ? currentnessLabels[target.currentness] : "현재성 미기록"} · 접근 {target.access_state}</p>
    </section>}
    {event.result && <section aria-label="실행 결과">
      <h5>실행 결과</h5>
      {event.result.summary_ko && <p>{event.result.summary_ko}</p>}
      {resultParts.length > 0 && <p>{resultParts.join(" · ")}</p>}
      {event.result.counts && Object.keys(event.result.counts).length > 0 && <SafeArguments value={event.result.counts} />}
      {event.result.reason_code && <code>{event.result.reason_code}</code>}
    </section>}
    {event.redaction?.applied && <section aria-label="보호된 정보">
      <h5>보호된 정보</h5>
      <p>{event.redaction.public_note_ko ?? "민감하거나 내부 전용인 값은 표시하지 않았습니다."}</p>
      {event.redaction.classes && event.redaction.classes.length > 0 && <p>{event.redaction.classes.join(" · ")}</p>}
    </section>}
  </div>;
}

export function ResearchActivityStrip({ events, detailOpen = false }: { events?: UserActivityEvent[]; detailOpen?: boolean }) {
  if (!events || events.length === 0) return null;
  const defaultEvents = events.filter((event) => event.visibility === "default").slice(-5);
  const summaryEvents = defaultEvents.length > 0 ? defaultEvents : events.slice(-5);
  return <section className="research-activity" aria-label="최근 작업과 자료 활동">
    <div className="research-activity-heading">
      <div><span>진행 근거</span><strong>최근 작업과 보고 있는 자료</strong></div>
      <span>{summaryEvents.length}건</span>
    </div>
    <ol className="research-activity-list">
      {summaryEvents.map((event) => <li key={event.event_id} data-severity={event.severity}>
        <div className="research-activity-icon">{stateIcon(event)}</div>
        <div className="research-activity-main">
          <strong>{event.label_ko}</strong>
          {targetLabel(event) && <span>{targetLabel(event)}</span>}
          <div className="research-activity-badges">
            <Tag minimal>{stateLabels[event.state]}</Tag>
            {event.research_relation !== "none" && <Tag minimal intent={event.research_relation === "contradicts" || event.research_relation === "stale" ? "warning" : "primary"}>{relationLabels[event.research_relation]}</Tag>}
          </div>
        </div>
      </li>)}
    </ol>
    <Disclosure label={`활동 상세 ${events.length}건`} defaultOpen={detailOpen}>
      <ol className="research-activity-expanded">
        {events.map((event) => <li key={event.event_id}>
          <header>
            {stateIcon(event)}<strong>{event.label_ko}</strong>
            <Tag minimal>{stateLabels[event.state]}</Tag>
            <Tag minimal>{relationLabels[event.research_relation]}</Tag>
          </header>
          <ActivityDetails event={event} />
        </li>)}
      </ol>
    </Disclosure>
  </section>;
}
