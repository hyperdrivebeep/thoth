import type { CompletedStage, ResearchStatus } from "./research";
import { sourceCardTitle } from "../sourceDisplay";

export type ProgressScene = {
  title: string;
  detail: string;
};

export type TimelineIcon = "tick-circle" | "document-open" | "cloud" | "search" | "form";

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

type WorkFacts = {
  focus: string | null;
  constraints: string[];
  locators: string[];
  hypotheses: string[];
  pendingHypotheses: string[];
  decidedHypotheses: string[];
  sources: string[];
};

const ACTION_COPY: Record<string, { label: string; icon: TimelineIcon }> = {
  stage_completed: { label: "단계 완료", icon: "tick-circle" },
  source_read: { label: "원문 위치", icon: "search" },
  source_attached: { label: "연결 자료", icon: "document-open" },
  model_call: { label: "모델 호출", icon: "cloud" },
  hypothesis_review: { label: "가설 검토", icon: "form" },
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

export function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) {
    return null;
  }
  if (ms < 1000) {
    return `${Math.round(ms)}ms`;
  }
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) {
    return `${totalSeconds}초`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds === 0 ? `${minutes}분` : `${minutes}분 ${seconds}초`;
}

function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) {
    return null;
  }
  if (bytes < 1024) {
    return `맥락 ${bytes.toLocaleString()}B`;
  }
  const kb = bytes / 1024;
  if (kb < 1024) {
    return `맥락 ${kb < 10 ? kb.toFixed(1) : Math.round(kb).toString()}KB`;
  }
  const mb = kb / 1024;
  return `맥락 ${mb < 10 ? mb.toFixed(1) : Math.round(mb).toString()}MB`;
}

function clip(text: string, max: number): string {
  const value = text.replace(/\s+/g, " ").trim();
  if (value.length <= max) {
    return value;
  }
  return `${value.slice(0, max - 1).trim()}…`;
}

function unique(items: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of items) {
    const value = item.replace(/\s+/g, " ").trim();
    if (!value || seen.has(value)) {
      continue;
    }
    seen.add(value);
    out.push(value);
  }
  return out;
}

function joinKo(items: string[]): string | null {
  if (items.length === 0) {
    return null;
  }
  if (items.length === 1) {
    return items[0];
  }
  return `${items.slice(0, -1).join(", ")}, ${items[items.length - 1]}`;
}

function quoted(items: string[], max = 3): string | null {
  return joinKo(items.slice(0, max).map((item) => `「${clip(item, 28)}」`));
}

function hangulBatchim(text: string): boolean | null {
  const last = text.trim().slice(-1);
  const code = last.charCodeAt(0);
  if (code < 0xac00 || code > 0xd7a3) {
    return null;
  }
  return (code - 0xac00) % 28 !== 0;
}

function eulreul(text: string): string {
  return hangulBatchim(text) === true ? `${text}을` : `${text}를`;
}

function iga(text: string): string {
  return hangulBatchim(text) === true ? `${text}이` : `${text}가`;
}

function wagwa(text: string): string {
  if (hangulBatchim(text) === true || /[0-9]/.test(text.trim().slice(-1))) {
    return `${text}과`;
  }
  return `${text}와`;
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
    found.push("표 숫자만 인용");
  }
  if (/추측 금지/u.test(text)) {
    found.push("추측 금지");
  }
  if (/원문 위치/u.test(text)) {
    found.push("원문 위치와 함께 답");
  }
  if (/확인 안 되면|부족한지|HOLD/u.test(text)) {
    found.push("확인 안 되면 부족 이유");
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
    locators: evidenceLabels(status).map((label) => label.replace(/\s+/g, " ").trim()),
    hypotheses,
    pendingHypotheses: unique(pending),
    decidedHypotheses: unique(decided),
    sources: unique(sourceUris.slice(0, 8).map((uri) => sourceCardTitle(uri))),
  };
}

function sceneFor(key: string | undefined, facts: WorkFacts, live = false): ProgressScene {
  const focus = facts.focus;
  const constraints = joinKo(facts.constraints.slice(0, 3));
  const locators = joinKo(facts.locators.slice(0, 3));
  const hyps = quoted(facts.hypotheses, 3);
  const decided = quoted(facts.decidedHypotheses, 2);
  const sources = joinKo(facts.sources.slice(0, 2));
  const constraintSubject = constraints ?? focus ?? "질문에서 꼭 지킬 조건";
  const locatorSubject = locators ?? (focus ? `${focus} 관련 문장` : null);
  const title = (done: string, doing: string) => (live ? doing : done);
  switch (key) {
    case "RESEARCH_PLANNER":
    case "REQUIREMENTS":
      return {
        title: title(`${eulreul(constraintSubject)} 고정했습니다`, `${eulreul(constraintSubject)} 고정하고 있습니다`),
        detail: "아직 답을 쓰지 않았습니다.",
      };
    case "EVIDENCE_RERANKER":
      return {
        title: title(
          locatorSubject
            ? `${wagwa(locatorSubject)} 관련된 문장을 다시 줄 세웠습니다`
            : sources
              ? `${sources}에서 ${focus ?? "질문"}과 관련된 문장을 다시 줄 세웠습니다`
              : `${focus ?? "질문"}과 관련된 문장을 다시 줄 세웠습니다`,
          locatorSubject
            ? `${wagwa(locatorSubject)} 관련된 문장을 다시 줄 세우고 있습니다`
            : `${focus ?? "질문"}과 관련된 문장을 다시 줄 세우고 있습니다`,
        ),
        detail: "순위만 매겼고, 지지 여부는 아직 판단하지 않았습니다.",
      };
    case "SEMANTIC_REVIEWER":
    case "EVIDENCE_REVIEW": {
      const condition = facts.constraints.find((item) => /조건/u.test(item)) ?? focus ?? "질문 조건";
      return {
        title: title(
          locators
            ? `${iga(condition)} ${locators} 원문에 있는지 대조했습니다`
            : `${eulreul(condition)} 원문과 대조했습니다`,
          locators
            ? `${iga(condition)} ${locators} 원문에 있는지 대조하고 있습니다`
            : `${eulreul(condition)} 원문과 대조하고 있습니다`,
        ),
        detail: "관련 있다고 해서 지지하는 것은 아닙니다.",
      };
    }
    case "REVIEW_ADJUDICATOR":
      return {
        title: title(
          `${focus ?? "조건"} 중 빠진 항목과 충돌을 판정했습니다`,
          `${focus ?? "조건"} 중 빠진 항목과 충돌을 판정하고 있습니다`,
        ),
        detail: "애매한 항목은 보류로 남깁니다.",
      };
    case "SOURCE_PLANNER":
      return {
        title: title(
          sources ? `${sources} 외에 더 열 주소를 골랐습니다` : "허용된 사이트에서 더 열 주소를 골랐습니다",
          sources ? `${sources} 외에 더 열 주소를 고르고 있습니다` : "허용된 사이트에서 더 열 주소를 고르고 있습니다",
        ),
        detail: "인터넷 전체를 검색하지 않습니다.",
      };
    case "HYPOTHESIS_GENERATOR":
    case "HYPOTHESES":
      return {
        title: title(
          hyps ? `${eulreul(hyps)} 준비했습니다` : `${focus ?? "질문"}에 대한 가능한 설명을 준비했습니다`,
          hyps ? `${eulreul(hyps)} 준비하고 있습니다` : `${focus ?? "질문"}에 대한 가능한 설명을 준비하고 있습니다`,
        ),
        detail: "아직 채택이 아닙니다.",
      };
    case "HYPOTHESIS_REVIEWER":
    case "HYPOTHESIS_REVIEW":
      return {
        title: title(
          decided
            ? `${eulreul(decided)} 원문과 대조했습니다`
            : hyps
              ? `${eulreul(hyps)} 원문과 대조했습니다`
              : "방금 만든 설명을 원문과 대조했습니다",
          decided
            ? `${eulreul(decided)} 원문과 대조하고 있습니다`
            : hyps
              ? `${eulreul(hyps)} 원문과 대조하고 있습니다`
              : "방금 만든 설명을 원문과 대조하고 있습니다",
        ),
        detail: "제안된 설명마다 결정이 있어야 검토가 끝납니다.",
      };
    case "ACTION_PLANNER":
    case "ACTION_COMPARISON":
      return {
        title: title(
          focus ? `${focus}를 확인하려면 다음에 할 작업을 골랐습니다` : "다음에 할 확인 작업을 골랐습니다",
          focus ? `${focus}를 확인하려면 다음에 할 작업을 고르고 있습니다` : "다음에 할 확인 작업을 고르고 있습니다",
        ),
        detail: "실행 전 초안입니다.",
      };
    case "EVIDENCE_EXTRACTOR":
    case "EVIDENCE_FOCUS":
      return {
        title: title(
          locators ? `${locators} 구절을 골랐습니다` : `${focus ?? "표와 원문"} 위치를 확인했습니다`,
          locators ? `${locators} 위치를 확인하고 있습니다` : `${focus ?? "표와 원문"} 위치를 확인하고 있습니다`,
        ),
        detail: "실제로 집어넣을 칸과 문장을 찾습니다.",
      };
    case "SUFFICIENCY_EXPLAINER":
      return {
        title: title(
          `${focus ?? "질문"}을 지금 근거로 답할 수 있는지 설명했습니다`,
          `${focus ?? "질문"}을 지금 근거로 답할 수 있는지 설명하고 있습니다`,
        ),
        detail: "부족한 항목은 보류 이유로 남깁니다.",
      };
    case "COUNTEREVIDENCE_CHALLENGER":
      return {
        title: title(
          hyps ? `${hyps}를 뒤집을 근거를 찾았습니다` : `${focus ?? "질문"}을 뒤집을 근거를 찾았습니다`,
          hyps ? `${hyps}를 뒤집을 근거를 찾고 있습니다` : `${focus ?? "질문"}을 뒤집을 근거를 찾고 있습니다`,
        ),
        detail: "지지 문장만 보지 않습니다.",
      };
    case "USER_EXPLAINER":
      return {
        title: title(
          `${focus ?? "현재 판단"}을 사람이 읽게 정리했습니다`,
          `${focus ?? "현재 판단"}을 사람이 읽게 정리하고 있습니다`,
        ),
        detail: "내부 코드명이 아니라 질문 기준으로 설명합니다.",
      };
    case "REFERENCE_MAPPER":
      return {
        title: title(
          locators ? `${locators} 인용을 원문 위치에 맞춰 두었습니다` : "인용과 원문 위치를 맞춰 두었습니다",
          locators ? `${locators} 인용을 원문 위치에 맞추고 있습니다` : "인용과 원문 위치를 맞추고 있습니다",
        ),
        detail: "표 번호와 셀 위치를 자료에 연결합니다.",
      };
    case "CONNECTED_SOURCES":
      return {
        title: title(
          sources ? `${sources}만 열고 있습니다` : "이 프로젝트에 허용된 자료만 열고 있습니다",
          sources ? `${sources}만 열고 있습니다` : "이 프로젝트에 허용된 자료만 열고 있습니다",
        ),
        detail: "웹 검색을 시작했다는 뜻이 아닙니다.",
      };
    case "SOURCE_SHORTLIST":
      return {
        title: title(
          sources
            ? `${sources}에서 질문과 맞는 원문 구간을 골랐습니다`
            : "연결 자료에서 질문과 맞는 원문 구간을 골랐습니다",
          sources
            ? `${sources}에서 질문과 맞는 원문 구간을 고르고 있습니다`
            : "연결 자료에서 질문과 맞는 원문 구간을 고르고 있습니다",
        ),
        detail: "시간 미확인 자료는 확정 근거가 아니라 후보입니다.",
      };
    case "HOLD":
      return {
        title: live
          ? "모델 응답을 기다리다 멈췄습니다"
          : "모델 호출이 끝나 연구를 멈췄습니다",
        detail: "연결 자료는 그대로 있고, 답을 확정하지 않았습니다.",
      };
    default:
      return {
        title: live
          ? `${focus ?? "요청"}을 받아 자료와 조건을 확인하고 있습니다`
          : `${focus ?? "요청"} 관련 단계를 마쳤습니다`,
        detail: "서버가 준 단계 이름만으로는 더 구체적인 작업을 특정하지 못했습니다.",
      };
  }
}

export function evidenceLabels(status?: ResearchStatus): string[] {
  const draft = asRecord(status?.attempt?.draft_progress);
  const focus = asRecord(draft?.evidence_focus);
  return asList(focus?.locators)
    .map((item) => {
      const locator = asRecord(item);
      if (!locator) {
        return "";
      }
      const page = locator.page == null ? "" : `p.${locator.page} `;
      const text = locator.exact_text == null ? "" : String(locator.exact_text);
      return `${page}${text}`.trim();
    })
    .filter((label) => label.length > 0);
}

function evidenceTotal(status?: ResearchStatus): number | null {
  const focus = asRecord(asRecord(status?.attempt?.draft_progress)?.evidence_focus);
  return numberOrNull(focus?.total);
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
    return `${model} · 추론 ${effort}`;
  }
  return model ?? `추론 ${effort}`;
}

function usageLine(status?: ResearchStatus): string | null {
  const usage = status?.usage;
  if (!usage) {
    return null;
  }
  const total = usage.total_tokens;
  const tokens = total == null ? "미확인" : total.toLocaleString();
  const partial = usage.state === "PARTIAL" ? " (부분 관측)" : "";
  const cached =
    usage.cached_input_tokens != null ? ` · 캐시 입력 ${usage.cached_input_tokens.toLocaleString()}` : "";
  return `사용 토큰 ${tokens}${partial}${cached}`;
}

function waitLine(status?: ResearchStatus): string | null {
  const dispatches = status?.model_dispatches ?? [];
  if (dispatches.length === 0) {
    return null;
  }
  const active =
    [...dispatches].reverse().find((item) => item.state === "RESERVED") ??
    dispatches[dispatches.length - 1];
  if (!active) {
    return null;
  }
  const observation = active.transport_observation;
  const elapsed = formatDuration(observation?.elapsed_ms ?? null);
  const firstByte = formatDuration(observation?.first_byte_ms ?? observation?.first_response_ms ?? null);
  const received = observation?.received_bytes ?? active.received_bytes;
  if (active.state === "RESERVED") {
    if (firstByte) {
      return `모델 응답 대기 · ${elapsed ?? "경과 미확인"} · 첫 글자 ${firstByte}`;
    }
    return `모델 응답 대기 · ${elapsed ?? "경과 미확인"} · 첫 글자 아직 없음`;
  }
  if (typeof received === "number") {
    return `마지막 모델 응답 ${received.toLocaleString()}B${elapsed ? ` · ${elapsed}` : ""}`;
  }
  return elapsed ? `마지막 모델 호출 · ${elapsed}` : null;
}

function missingFor(
  status: ResearchStatus,
  facts: WorkFacts,
  labels: string[],
  total: number | null,
  hypotheses: number | null,
  reviews: number | null,
): string | null {
  const phase = status.attempt?.phase ?? "";
  if (phase === "EVIDENCE_FOCUS" && labels.length === 0 && (total === 0 || total == null)) {
    return `${facts.focus ?? "질문"}에 집어넣을 표·문장이 아직 0곳입니다. 붙인 로컬 원문이 없어 웹 검색은 이 단계에서 시작하지 않습니다.`;
  }
  if (phase === "EVIDENCE_FOCUS" && labels.length > 0) {
    const found = joinKo(labels.slice(0, 3));
    return `${found} ${labels.length}곳을 확인했습니다${total != null && total > labels.length ? ` · 전체 ${total}곳 중 일부` : ""}.`;
  }
  if ((phase === "HYPOTHESIS_REVIEW" || phase === "HYPOTHESES") && hypotheses != null) {
    const pending = quoted(facts.pendingHypotheses, 2);
    const decided = quoted(facts.decidedHypotheses, 2);
    if (reviews == null || reviews === 0) {
      return pending
        ? `${pending}부터 대조하겠습니다. 빠진 ${hypotheses}개 결정을 채우기 전입니다.`
        : `가설 ${hypotheses}개 · 검토 결정은 아직 없습니다.`;
    }
    if (reviews < hypotheses) {
      const rest = hypotheses - reviews;
      if (pending && decided) {
        return `${decided}는 봤고, ${pending} ${rest}개는 아직입니다. 빠진 결정을 채우겠습니다.`;
      }
      return pending
        ? `${pending} ${rest}개가 빠졌습니다. 빠진 결정을 채우겠습니다.`
        : `가설 ${hypotheses}개 중 ${reviews}개만 결정됐습니다. 빠진 ${rest}개를 채우겠습니다.`;
    }
    return decided ? `${decided}까지 대조했습니다.` : `가설 ${hypotheses}개 · 검토 ${reviews}개`;
  }
  if (phase === "CONNECTED_SOURCES") {
    return facts.sources.length > 0
      ? `${joinKo(facts.sources.slice(0, 2))} 목록을 여는 중이며, 답을 쓰기 전입니다.`
      : "연결 자료 목록을 여는 중이며, 답을 쓰기 전입니다.";
  }
  if (phase === "SOURCE_SHORTLIST") {
    return facts.sources.length > 0
      ? `${joinKo(facts.sources.slice(0, 2))}에서 원문 구간을 고르는 중이며, 답을 쓰기 전입니다.`
      : "연결 자료에서 원문 구간을 고르는 중이며, 답을 쓰기 전입니다.";
  }
  if (phase === "HOLD") {
    const reason = status.current_result?.terminal_reason ?? "";
    if (reason.includes("429")) {
      return "계정 사용 한도로 모델 호출이 거절됐습니다.";
    }
    if (reason.includes("AUTH_REQUIRED") || reason.includes("403")) {
      return "모델 계정 인증이 거절됐습니다.";
    }
    if (reason.includes("MODEL_NOT_SUPPORTED")) {
      return "이 ChatGPT Codex 계정에서 지원하지 않는 모델입니다.";
    }
    if (reason.includes("400")) {
      return "모델이 요청을 거절했습니다.";
    }
    return "답을 쓰기 전에 모델 호출이 멈췄습니다.";
  }
  return null;
}

function compactPayload(parts: Array<string | null | undefined>): string | null {
  const values = parts.filter((item): item is string => Boolean(item && item.trim()));
  return values.length > 0 ? values.join(" · ") : null;
}

function locatorPayload(page: unknown, text: unknown): string | null {
  const pageLabel = page == null ? "" : `p.${page}`;
  const excerpt = text == null ? "" : String(text).trim();
  return compactPayload([pageLabel, excerpt]);
}

function stageActionPayload(stage: CompletedStage): string | null {
  const dispatchCount = Array.isArray(stage.dispatch_ids) ? stage.dispatch_ids.length : null;
  return compactPayload([
    formatDuration(stage.elapsed_ms),
    formatBytes(stage.context_bytes),
    dispatchCount != null && dispatchCount > 0 ? `모델 ${dispatchCount}회` : null,
  ]);
}

function modelActionPayload(status?: ResearchStatus): { payload: string | null; live: boolean } | null {
  const dispatches = status?.model_dispatches ?? [];
  if (dispatches.length === 0) {
    return null;
  }
  const active =
    [...dispatches].reverse().find((item) => item.state === "RESERVED") ??
    dispatches[dispatches.length - 1];
  if (!active) {
    return null;
  }
  const observation = active.transport_observation;
  const elapsed = formatDuration(observation?.elapsed_ms ?? null);
  const firstByte = formatDuration(observation?.first_byte_ms ?? observation?.first_response_ms ?? null);
  const received = observation?.received_bytes ?? active.received_bytes;
  if (active.state === "RESERVED") {
    return {
      live: true,
      payload: compactPayload([
        elapsed ?? "경과 미확인",
        firstByte ? `첫 글자 ${firstByte}` : "첫 글자 아직 없음",
      ]),
    };
  }
  return {
    live: false,
    payload: compactPayload([
      typeof received === "number" ? `${received.toLocaleString()}B` : null,
      elapsed,
    ]),
  };
}

function hypothesisActionPayload(hypotheses: number | null, reviews: number | null): string | null {
  if (hypotheses == null) {
    return null;
  }
  if (reviews == null) {
    return `가설 ${hypotheses}개 · 결정은 아직 없습니다`;
  }
  if (reviews < hypotheses) {
    return `가설 ${hypotheses}개 중 ${reviews}개만 결정`;
  }
  return `가설 ${hypotheses}개 · 검토 ${reviews}개`;
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
              ? `모델 ${payload.dispatch_count}회`
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
          locatorPayload(payload.page, payload.exact_text),
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
            ? compactPayload([elapsed ?? "경과 미확인", firstByte ? `첫 글자 ${firstByte}` : "첫 글자 아직 없음"])
            : compactPayload([received != null ? `${received.toLocaleString()}B` : null, elapsed]),
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
  const missing = missingFor(status, facts, labels, total, hypotheses, reviews);
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
    return "현재 단계를 마치고 멈추는 중입니다.";
  }
  if (status.execution_state === "PAUSED") {
    return "작업이 일시정지되어 있습니다.";
  }
  if (status.attempt?.remote_observation === "UNKNOWN" && status.attempt.external_effect_state === "DISPATCHING") {
    return "외부 실행의 종료 여부를 아직 확인하지 못했습니다.";
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
