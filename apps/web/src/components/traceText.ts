import type { ChangedDependency, ImportIssue, TraceResult, TraceRule, VerdictChange } from "../api/trace";
import { resultText, ruleText } from "./traceRows";
import type { ReasonTagId } from "./traceReasonTags";

/** Plain-language text for the trace table. Raw codes stay in the technical details, never in the main text. */

export type Look = { text: string; intent: "success" | "danger" | "warning" | "none"; icon: "tick-circle" | "cross-circle" | "warning-sign" | "help" };

const criterionLooks: Record<string, Look> = {
  PASS_COMPUTED: { text: "기준 충족", intent: "success", icon: "tick-circle" },
  FAIL_COMPUTED: { text: "기준 미달", intent: "danger", icon: "cross-circle" },
  HOLD_NO_RESULT: { text: "보류 · 결과 없음", intent: "warning", icon: "warning-sign" },
  HOLD_INVALID_RESULT: { text: "보류 · 결과를 쓸 수 없음", intent: "warning", icon: "warning-sign" },
  HOLD_NO_RULE: { text: "보류 · 판정 규칙 없음", intent: "warning", icon: "warning-sign" },
};
const requirementLooks: Record<string, Look> = {
  PASS: { text: "모든 필수 기준 충족", intent: "success", icon: "tick-circle" },
  FAIL: { text: "기준 미달", intent: "danger", icon: "cross-circle" },
  FAIL_WITH_INCOMPLETE_COVERAGE: { text: "기준 미달 · 결과 없는 조건도 있음", intent: "danger", icon: "cross-circle" },
  HOLD_INCOMPLETE: { text: "보류 · 결과 없는 조건이 있음", intent: "warning", icon: "warning-sign" },
  HOLD_NO_CRITERIA: { text: "보류 · 필수 기준이 없음", intent: "warning", icon: "warning-sign" },
};
export const verdictLook = (kind: string, state: string): Look =>
  (kind === "REQUIREMENT" ? requirementLooks : criterionLooks)[state] ?? { text: "알 수 없는 판정", intent: "none", icon: "help" };

/** Who decides after the check. Provisional names, to be confirmed with the people who do this work. */
export const NEXT_STEP_DECIDERS = { test: "시험 책임자", validity: "지정 검증자", rule: "요구사항 담당자", disposition: "해당 권한자" } as const;

/** One thing to check, and who decides after it. A line can have its own decider; null when no one is named. */
export type NextLine = { text: string; decider: string | null };
export type NextStep = { lines: NextLine[] };
const line = (text: string, decider: string | null): NextLine => ({ text, decider });
const STALE_CHECK = "바뀐 근거로 판정을 다시 볼지 확인합니다. 이전 안내는 이전 근거 기준입니다.";
const INVESTIGATE = "시험이 유효했는지, 다른 원인 후보가 있는지 확인합니다.";

const criterionNext: Record<string, NextLine[]> = {
  HOLD_NO_RESULT: [line("이 조건의 시험 결과가 있는지, 있다면 추적표에 연결됐는지 확인합니다. 없으면 추가 시험 계획을 검토합니다.", NEXT_STEP_DECIDERS.test)],
  HOLD_INVALID_RESULT: [line("결과의 ID·단위·분모·시험 조건을 원자료와 대조하고, 그 결과를 쓸지 정합니다.", NEXT_STEP_DECIDERS.validity)],
  HOLD_NO_RULE: [line("요구사항 원문에서 승인된 판정 규칙과 그 버전을 확인해 규칙을 확정합니다.", NEXT_STEP_DECIDERS.rule)],
  FAIL_COMPUTED: [line(INVESTIGATE, NEXT_STEP_DECIDERS.disposition)],
};
/** A more specific check for each reason tag of a held line; with none of these tags the state's general check above is used. */
const tagNext: Record<string, Partial<Record<ReasonTagId, NextLine>>> = {
  HOLD_NO_RESULT: {
    UNTESTED: line("이 기준에 연결된 시험의 결과가 아직 들어오지 않았습니다. 시험이 진행됐는지 확인하고, 결과가 없으면 시험 계획을 검토합니다.", NEXT_STEP_DECIDERS.test),
    UNLINKED: line("이 기준을 확인할 시험이 연결돼 있지 않습니다. 이 기준을 시험으로 확인해야 하는지 정하고, 필요하면 시험과 연결합니다.", NEXT_STEP_DECIDERS.rule),
    CONDITION_MISMATCH: line("결과는 있지만 기준의 조건과 달라 쓰지 않았습니다. 기준의 조건이 맞는지, 이 조건으로 시험이 더 필요한지 확인합니다.", NEXT_STEP_DECIDERS.test),
  },
  HOLD_INVALID_RESULT: {
    UNIT: line("결과의 단위가 기준의 단위와 다릅니다. 원자료와 대조해 어느 쪽 단위가 맞는지 확인하고, 그 결과를 쓸지 정합니다.", NEXT_STEP_DECIDERS.validity),
    DENOMINATOR: line("결과의 분모가 0 이하이거나, 값이 분자·분모로 계산한 값과 다릅니다. 분자·분모와 값을 원자료와 대조해 어느 쪽이 틀렸는지 확인합니다.", NEXT_STEP_DECIDERS.validity),
    VALUE: line("결과의 값이 비었거나 숫자가 아니거나 너무 큽니다. 원자료에서 값을 다시 확인해 결과를 고칠지 정합니다.", NEXT_STEP_DECIDERS.validity),
  },
};
const requirementNext: Record<string, NextLine[]> = {
  FAIL: [line("미달인 기준 줄에서 " + INVESTIGATE, NEXT_STEP_DECIDERS.disposition)],
  FAIL_WITH_INCOMPLETE_COVERAGE: [
    line("미달인 기준 줄에서 " + INVESTIGATE, NEXT_STEP_DECIDERS.disposition),
    line("아직 시험하지 않은 조건은 시험 계획을 따로 세웁니다. 미달 조사가 끝나도 이 계획은 남습니다.", NEXT_STEP_DECIDERS.test),
  ],
  HOLD_INCOMPLETE: [line("결과가 없는 조건은 각 기준 줄의 '다음 확인'을 따릅니다.", null)],
  HOLD_NO_CRITERIA: [line("이 요구사항에 필수 기준이 정의돼 있는지 확인합니다.", NEXT_STEP_DECIDERS.rule)],
};
const settled = new Set(["PASS_COMPUTED", "PASS"]);
const TAG_ORDER: ReasonTagId[] = ["UNTESTED", "UNLINKED", "CONDITION_MISMATCH", "UNIT", "DENOMINATOR", "VALUE"];

/**
 * What to check next for a held or failed line, from its state and its reason tags. A held criterion gets one line per tag it
 * carries (no tag, or none this table knows, gives the state's general check). A met line has none unless its basis changed.
 * An unknown state gets nothing, never a guess.
 */
export function nextStep(kind: "CRITERION" | "REQUIREMENT", state: string, stale: boolean, tags: readonly ReasonTagId[] = []): NextStep | null {
  const general = (kind === "REQUIREMENT" ? requirementNext : criterionNext)[state];
  if (!general && !settled.has(state)) return null;
  const specific = kind === "CRITERION" ? TAG_ORDER.flatMap(id => (tags.includes(id) && tagNext[state]?.[id] ? [tagNext[state]![id]!] : [])) : [];
  const own = specific.length > 0 ? specific : general ?? [];
  if (!stale) return own.length > 0 ? { lines: own.map(item => ({ ...item })) } : null;
  return { lines: [line(STALE_CHECK, null), ...own.map(item => ({ ...item }))] };
}
const triggers: Record<string, string> = { INITIAL_COMPUTE: "처음 계산", CSV_IMPORT: "CSV 들여오기 반영", RECOMPUTE: "다시 계산" };
export const triggerLabel = (trigger: string) => triggers[trigger] ?? "다른 이유로 다시 계산";

export function actorLabel(actorId: string) {
  if (actorId === "system:verification-trace") return "THOTH 계산";
  if (actorId === "human:local-user") return "이 컴퓨터의 사용자";
  return actorId;
}
export const timeText = (iso: string) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("ko-KR");
};

/** One reason the rule found, as a sentence. `titleOf` names a criterion for the reasons a requirement carries. */
export function reasonSentence(code: string, titleOf: (id: string) => string, condition?: string | null): string {
  const [head, ...rest] = code.split(":");
  const tail = rest.join(":");
  const known: Record<string, string> = {
    NO_RULE: "이 기준에는 판정 규칙이 없어 비교하지 않았습니다.",
    NO_REQUIRED_CRITERIA: "필수 기준이 하나도 없어 통과로 보지 않습니다.",
  };
  if (known[head]) return known[head];
  if (head === "NO_RESULT") {
    // The condition comes from the verdict's own condition field; the text after the colon is only the fallback.
    const text = condition ?? tail;
    return text ? `${describeConditionText(text)} 조건의 결과가 아직 없습니다.` : "결과가 아직 없습니다.";
  }
  if (head === "THRESHOLD_NOT_MET") {
    const match = /value=(\S+) need (>=|<=) (\S+) (.*)$/.exec(tail);
    if (!match) return "측정값이 기준을 만족하지 못합니다.";
    const unit = match[4] ? "(" + match[4] + ")" : "";
    return match[2] === ">="
      ? "측정값 " + match[1] + "이(가) 기준 " + match[3] + " 이상" + unit + "에 못 미칩니다."
      : "측정값 " + match[1] + "이(가) 기준 " + match[3] + " 이하" + unit + "를 넘습니다.";
  }
  if (head === "UNIT_MISMATCH") {
    const match = /:([^!]*)!=(.*)$/.exec(tail);
    return match ? `결과의 단위(${match[1]})가 기준의 단위(${match[2]})와 달라 비교하지 않았습니다.` : "결과의 단위가 기준과 달라 비교하지 않았습니다.";
  }
  const simple: Record<string, string> = {
    VALUE_MISSING: "결과에 값이 비어 있어 비교하지 않았습니다.",
    VALUE_NOT_A_NUMBER: "결과의 값이 숫자가 아니어서 비교하지 않았습니다.",
    VALUE_DISAGREES_WITH_COUNTS: "결과의 값이 분자·분모로 계산한 값과 달라 비교하지 않았습니다.",
    DENOMINATOR_NOT_POSITIVE: "결과의 분모가 0 이하여서 비교하지 않았습니다.",
    VALUE_OUT_OF_RANGE: "결과의 값이 너무 커서 계산하지 못했습니다.",
  };
  if (simple[head]) return simple[head];
  if (rest.length > 0 && criterionLooks[rest.join(":")]) return `${titleOf(head)}: ${criterionLooks[rest.join(":")].text}`;
  return "이유를 문장으로 바꾸지 못했습니다. 기술 정보를 확인하세요.";
}

const fieldNames: Record<string, string> = {
  value: "측정값", unit: "단위", condition: "조건", numerator: "분자", denominator: "분모", result_revision: "결과 개정",
  source_span_refs: "근거 위치", raw_value: "원문 값", observed_at: "관측 시각", criterion_id: "대상 기준", threshold: "기준값",
  comparator: "비교 방향", required: "필수 여부", rounding: "반올림", measure: "측정 항목", rule_revision: "규칙 개정", policy: "결과 선택 정책",
};
const shown = (value: string | null) => (value === null || value === "" ? "비어 있음" : value);

const conditionKeys: Record<string, { name: string; values: Record<string, string> }> = {
  weather: { name: "날씨", values: { dry: "맑음(건조)", rain: "비", fog: "안개" } },
};
/** A condition the user wrote as a key and a value, in plain words. A key or value this screen does not know is shown as written. */
export function describeCondition(key: string, value: string): string {
  const known = conditionKeys[key];
  if (!known) return `${key} = ${value}`;
  return `${known.name}: ${known.values[value] ?? value}`;
}
export function describeConditionText(text: string): string {
  const at = text.indexOf("=");
  return at < 0 ? text : describeCondition(text.slice(0, at).trim(), text.slice(at + 1).trim());
}

export type ChangeLookup = { results: TraceResult[]; rules: TraceRule[] };

/** One input change as a sentence. What was added is described from the stored result or rule, never from the text the server wrote. */
export function changeSentence(change: ChangedDependency, lookup?: ChangeLookup): string {
  const name = change.ref_id;
  const pair = (): [string | null, string | null] => (change.field === "condition"
    ? [change.before, change.after].map(item => (item ? describeConditionText(item) : item)) as [string | null, string | null]
    : [change.before, change.after]);
  if (change.kind === "RESULT") {
    if (change.field === "*") {
      if (change.before !== null) return `결과 삭제: ${name}`;
      const result = lookup?.results.find(item => item.result_id === name);
      return result ? `결과 추가: ${name} (${resultText(result)} · ${describeConditionText(result.condition)})` : `결과 추가: ${name}`;
    }
    const [before, after] = pair();
    return `결과 ${name}의 ${fieldNames[change.field] ?? "내용"}: ${shown(before)} → ${shown(after)}`;
  }
  if (change.kind === "RULE") {
    if (change.field === "*") {
      if (change.before !== null) return "판정 규칙 삭제";
      const rule = lookup?.rules.find(item => item.rule_id === name);
      return rule ? `판정 규칙 추가: ${ruleText(rule)} · ${describeConditionText(rule.condition)}` : "판정 규칙 추가";
    }
    const [before, after] = pair();
    return `판정 규칙의 ${fieldNames[change.field] ?? "내용"}: ${shown(before)} → ${shown(after)}`;
  }
  if (change.kind === "LINK") return change.before === null ? `요구사항 연결 추가: ${name}` : `요구사항 연결 삭제: ${name}`;
  return "결과 선택 정책이 바뀌었습니다.";
}

export const verdictChangeSentence = (change: VerdictChange, titleOf: (id: string) => string) =>
  `${titleOf(change.subject_id)}: ${change.before === null ? "새로 계산됨" : verdictLook(change.subject_kind, change.before).text} → ${verdictLook(change.subject_kind, change.after).text}`;

export const resultReasonText = (reason: string) =>
  reason === "SUPERSEDED_BY_LATER_RESULT" ? "더 최근 결과가 있어 제외" : reason === "CONDITION_MISMATCH" ? "조건이 달라 제외" : "제외";

const lossTexts: Record<string, string> = {
  evidence_text: "근거 문장 자체(위치만 담깁니다)",
  verdict_history: "판정 이력(들여온 뒤 다시 계산됩니다)",
  confirmations: "누가 어떤 판정을 확인했는지",
  selection_policy: "결과를 고르는 정책",
  pending_changes: "다시 계산이 필요한 항목 표시",
  closures: "처분 표시 열(읽기 전용이라 고쳐도 반영되지 않습니다. 처분은 THOTH에서 기록하세요)",
};
export function lossText(line: string) {
  const key = line.split(":")[0];
  if (lossTexts[key]) return lossTexts[key];
  return line.startsWith("column ignored:") ? `무시한 열: ${line.slice(line.indexOf(":") + 1).trim()}` : line;
}

export type IssueText = { message: string; next: string };
const where = (issue: ImportIssue) => (issue.row ? ` (${issue.row}번째 줄)` : "");
const ref = (issue: ImportIssue) => (issue.ref ? ` ${issue.ref}` : "");

export function issueText(issue: ImportIssue): IssueText {
  const table: Record<string, () => IssueText> = {
    STALE_BASE: () => ({ message: "이 파일은 지금의 추적표와 다른 상태에서 내보낸 것입니다. 그 뒤에 추적표가 바뀌었습니다.", next: "지금 상태에서 'CSV로 내보내기'를 다시 한 뒤, 그 파일을 고쳐서 들여오세요." }),
    BASE_DIGEST_MISSING: () => ({ message: "파일에 어느 상태에서 내보낸 것인지 표시가 없습니다.", next: "'CSV로 내보내기'로 받은 파일을 그대로 고쳐 쓰세요. 머리 줄과 기준 상태 열은 지우지 마세요." }),
    BASE_DIGEST_INCONSISTENT: () => ({ message: "한 파일 안에 서로 다른 상태에서 내보낸 줄이 섞여 있습니다.", next: "한 번에 내보낸 파일 하나만 고치세요." }),
    MODE_CREATE_NEEDS_EMPTY_PROJECT: () => ({ message: "이 프로젝트에는 이미 추적표가 있어 '새로 만들기'를 할 수 없습니다.", next: "'기존 표 고치기'를 선택하세요." }),
    UPDATE_NEEDS_EXISTING_TRACE: () => ({ message: "고칠 추적표가 아직 없습니다.", next: "'새로 만들기'를 선택하세요." }),
    DUPLICATE_ID_IN_FILE: () => ({ message: `같은 ID${ref(issue)}가 파일에 두 번 이상 있습니다${where(issue)}.`, next: "중복된 줄을 하나로 정리하세요." }),
    ROW_INVALID: () => ({ message: `${issue.row ?? "?"}번째 줄${ref(issue)}의 값을 읽지 못했습니다. 칸의 형식이 맞는지 확인하세요.`, next: "숫자 칸에는 숫자만, 종류 칸에는 정해진 값만 쓰세요." }),
    ITEM_KEY_CHANGED: () => ({ message: `항목${ref(issue)}의 내부 키는 바꿀 수 없습니다${where(issue)}.`, next: "내부 키 칸은 내보낸 값 그대로 두세요." }),
    DELETE_TARGET_NOT_FOUND: () => ({ message: `지우려는 것${ref(issue)}이(가) 추적표에 없습니다${where(issue)}.`, next: "삭제 줄의 종류와 ID를 확인하세요." }),
    DELETE_BLOCKED_BY_LINK: () => ({ message: `항목${ref(issue)}에는 아직 연결이 남아 있어 지울 수 없습니다.`, next: "연결 줄의 삭제 줄도 함께 넣거나, 항목 삭제를 빼세요." }),
    DELETE_BLOCKED_BY_RULE: () => ({ message: `기준${ref(issue)}에는 판정 규칙이 남아 있어 지울 수 없습니다.`, next: "규칙의 삭제 줄도 함께 넣거나, 기준 삭제를 빼세요." }),
    DELETE_BLOCKED_BY_RESULT: () => ({ message: `항목${ref(issue)}에는 결과가 남아 있어 지울 수 없습니다.`, next: "결과의 삭제 줄도 함께 넣거나, 삭제를 빼세요." }),
    DELETE_AND_UPSERT_SAME_TARGET: () => ({ message: `${ref(issue)}을(를) 같은 파일에서 고치면서 지우려 합니다${where(issue)}.`, next: "둘 중 하나만 남기세요." }),
    DELETE_NOT_ALLOWED_IN_CREATE: () => ({ message: "'새로 만들기'에는 삭제 줄을 쓸 수 없습니다.", next: "삭제 줄을 빼거나 '기존 표 고치기'를 선택하세요." }),
    DELETE_ROW_INCOMPLETE: () => ({ message: `삭제 줄에 종류나 ID가 비어 있습니다${where(issue)}.`, next: "삭제 줄에는 종류와 ID를 모두 쓰세요." }),
    EXCEL_ID_SUSPECTED: () => ({ message: `ID${ref(issue)}는 스프레드시트가 바꾼 것처럼 보입니다${where(issue)}. ${issue.detail}`, next: "원래 ID로 되돌리고, ID 칸을 '텍스트' 서식으로 바꾼 뒤 다시 저장하세요." }),
    GRAPH_INVALID: () => ({ message: "반영하면 연결이 맞지 않는 곳이 생깁니다.", next: "연결 줄의 양쪽 ID와 종류가 추적표에 있는지 확인하세요." }),
    HEADER_MISSING_COLUMNS: () => ({ message: "파일의 머리 줄에 꼭 필요한 열이 빠져 있습니다.", next: "'CSV로 내보내기'로 받은 파일의 머리 줄을 지우지 말고 쓰세요." }),
    HEADER_DUPLICATE_COLUMN: () => ({ message: "머리 줄에 같은 열 이름이 두 번 있습니다.", next: "중복된 열을 지우세요." }),
    FORMAT_UNSUPPORTED: () => ({ message: `이 화면이 읽는 형식의 파일이 아닙니다${where(issue)}.`, next: "'CSV로 내보내기'로 받은 파일을 쓰세요." }),
    ROW_TYPE_UNKNOWN: () => ({ message: `줄의 종류를 알 수 없습니다${where(issue)}.`, next: "줄의 종류는 항목·연결·규칙·결과·삭제 중 하나여야 합니다." }),
    ID_MISSING: () => ({ message: `ID가 비어 있는 줄이 있습니다${where(issue)}.`, next: "ID 칸을 채우거나 그 줄을 지우세요." }),
    CSV_EMPTY: () => ({ message: "파일이 비어 있습니다.", next: "내용이 있는 파일을 고르세요." }),
    CSV_HAS_NO_ROWS: () => ({ message: "파일에 들여올 줄이 없습니다.", next: "내용이 있는 파일을 고르세요." }),
    FILE_TOO_LARGE: () => ({ message: "파일이 너무 큽니다.", next: "파일을 나누어 들여오세요." }),
    TOO_MANY_ROWS: () => ({ message: "줄이 너무 많습니다.", next: "파일을 나누어 들여오세요." }),
    CSV_UNREADABLE: () => ({ message: "CSV로 읽을 수 없는 파일입니다.", next: "UTF-8 CSV 파일인지 확인하세요." }),
  };
  return (table[issue.code] ?? (() => ({ message: "이 파일을 반영할 수 없는 문제가 있습니다.", next: "기술 정보를 확인한 뒤 파일을 고쳐 다시 시도하세요." })))();
}

/** An apply that the server refused, in words and with what to do next. */
export function applyFailureText(error: unknown): IssueText {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("TRACE_IMPORT_PREVIEW_STALE")) return { message: "미리보기를 본 뒤에 추적표가 바뀌어 반영하지 않았습니다.", next: "파일을 다시 선택해 미리보기부터 다시 하세요." };
  if (message.includes("TRACE_IMPORT_INPUT_CHANGED")) return { message: "미리보기에서 본 파일과 반영하려는 파일이 달라 반영하지 않았습니다.", next: "파일을 다시 선택해 미리보기부터 다시 하세요." };
  if (message.includes("TRACE_IMPORT_NOT_APPLICABLE")) return { message: "파일에 해결되지 않은 문제가 있어 반영하지 않았습니다.", next: "미리보기의 문제를 고친 파일로 다시 시도하세요." };
  if (message.includes("TRACE_REVISION_CONFLICT")) return { message: "다른 곳에서 먼저 추적표가 바뀌어 반영하지 않았습니다.", next: "화면을 새로 읽은 뒤 다시 시도하세요." };
  if (message.includes("closing or archived")) return { message: "닫는 중이거나 보관된 프로젝트에서는 추적표를 바꿀 수 없습니다.", next: "프로젝트 상태를 확인하세요." };
  return { message: "반영하지 못했습니다. 아무것도 저장되지 않았습니다.", next: "잠시 뒤 다시 시도하세요." };
}
