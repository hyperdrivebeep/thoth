import { message } from "../i18n/formatMessages";
import type { MessageKey } from "../i18n/messages";
import { t, getLocale, formatDate, type Locale } from "../i18n/locale";
import { criterionVerdicts, requirementVerdicts, reasonEnglish } from "../i18n/traceEnglish";
import { iga } from "../api/koreanParticles";
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
export const verdictLook = (kind: string, state: string, locale: Locale = getLocale()): Look => {
  const look = (kind === "REQUIREMENT" ? requirementLooks : criterionLooks)[state] ?? { text: "알 수 없는 판정", intent: "none", icon: "help" };
  return locale === "en" ? { ...look, text: (kind === "REQUIREMENT" ? requirementVerdicts : criterionVerdicts)[state] ?? "Unknown assessment" } : look;
};

/** Who decides after the check. Provisional names, to be confirmed with the people who do this work. */
export const NEXT_STEP_DECIDERS = { get test() { return t("trace.decider.test"); }, get validity() { return t("trace.decider.validity"); }, get rule() { return t("trace.decider.rule"); }, get disposition() { return t("trace.decider.disposition"); } } as const;

/** One thing to check, and who decides after it. A line can have its own decider; null when no one is named. */
export type NextLine = { text: string; decider: string | null };
export type NextStep = { lines: NextLine[] };
const line = (text: string, decider: string | null): NextLine => ({ text, decider });
const STALE_CHECK = () => t("trace.next.stale");
const INVESTIGATE = () => t("trace.next.investigate");

const criterionNext = (): Record<string, NextLine[]> => ({
  HOLD_NO_RESULT: [line(t("trace.next.noResult"), NEXT_STEP_DECIDERS.test)],
  HOLD_INVALID_RESULT: [line(t("trace.next.invalid"), NEXT_STEP_DECIDERS.validity)],
  HOLD_NO_RULE: [line(t("trace.next.rule"), NEXT_STEP_DECIDERS.rule)],
  FAIL_COMPUTED: [line(INVESTIGATE(), NEXT_STEP_DECIDERS.disposition)],
});
/** A more specific check for each reason tag of a held line; with none of these tags the state's general check above is used. */
const tagNext = (): Record<string, Partial<Record<ReasonTagId, NextLine>>> => ({
  HOLD_NO_RESULT: {
    UNTESTED: line(t("trace.next.untested"), NEXT_STEP_DECIDERS.test),
    UNLINKED: line(t("trace.next.unlinked"), NEXT_STEP_DECIDERS.rule),
    CONDITION_MISMATCH: line(t("trace.next.condition"), NEXT_STEP_DECIDERS.test),
  },
  HOLD_INVALID_RESULT: {
    UNIT: line(t("trace.next.unit"), NEXT_STEP_DECIDERS.validity),
    DENOMINATOR: line(t("trace.next.denominator"), NEXT_STEP_DECIDERS.validity),
    VALUE: line(t("trace.next.value"), NEXT_STEP_DECIDERS.validity),
  },
});
const requirementNext = (): Record<string, NextLine[]> => ({
  FAIL: [line(t("trace.failedCheck"), NEXT_STEP_DECIDERS.disposition)],
  FAIL_WITH_INCOMPLETE_COVERAGE: [
    line(t("trace.failedCheck"), NEXT_STEP_DECIDERS.disposition),
    line(t("trace.next.additionalTests"), NEXT_STEP_DECIDERS.test),
  ],
  HOLD_INCOMPLETE: [line(t("trace.next.followRow"), null)],
  HOLD_NO_CRITERIA: [line(t("trace.next.required"), NEXT_STEP_DECIDERS.rule)],
});
const settled = new Set(["PASS_COMPUTED", "PASS"]);
const TAG_ORDER: ReasonTagId[] = ["UNTESTED", "UNLINKED", "CONDITION_MISMATCH", "UNIT", "DENOMINATOR", "VALUE"];

/**
 * What to check next for a held or failed line, from its state and its reason tags. A held criterion gets one line per tag it
 * carries (no tag, or none this table knows, gives the state's general check). A met line has none unless its basis changed.
 * An unknown state gets nothing, never a guess.
 */
export function nextStep(kind: "CRITERION" | "REQUIREMENT", state: string, stale: boolean, tags: readonly ReasonTagId[] = []): NextStep | null {
  const general = (kind === "REQUIREMENT" ? requirementNext() : criterionNext())[state];
  if (!general && !settled.has(state)) return null;
  const specific = kind === "CRITERION" ? TAG_ORDER.flatMap(id => (tags.includes(id) && tagNext()[state]?.[id] ? [tagNext()[state]![id]!] : [])) : [];
  const own = specific.length > 0 ? specific : general ?? [];
  if (!stale) return own.length > 0 ? { lines: own.map(item => ({ ...item })) } : null;
  return { lines: [line(STALE_CHECK(), null), ...own.map(item => ({ ...item }))] };
}
const triggers: Record<string, string> = { get INITIAL_COMPUTE() { return t("trace.trigger.initial"); }, get CSV_IMPORT() { return t("trace.trigger.import"); }, get RECOMPUTE() { return t("trace.trigger.recompute"); } };
export const triggerLabel = (trigger: string) => triggers[trigger] ?? t("trace.trigger.other");

export function actorLabel(actorId: string) {
  if (actorId === "system:verification-trace") return t("trace.actor.system");
  if (actorId === "human:local-user") return t("trace.actor.local");
  return actorId;
}
export const timeText = (iso: string) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : formatDate(date);
};

/** One reason the rule found, as a sentence. `titleOf` names a criterion for the reasons a requirement carries. */
export function reasonSentence(code: string, titleOf: (id: string) => string, condition?: string | null, locale: Locale = getLocale()): string {
  if (locale === "en") return reasonEnglish(code, titleOf, condition);
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
    return text ? `${describeConditionText(text, locale)} 조건의 결과가 아직 없습니다.` : "결과가 아직 없습니다.";
  }
  if (head === "THRESHOLD_NOT_MET") {
    const match = /value=(\S+) need (>=|<=) (\S+) (.*)$/.exec(tail);
    if (!match) return "측정값이 기준을 만족하지 못합니다.";
    const unit = match[4] ? "(" + match[4] + ")" : "";
    return match[2] === ">="
      ? "측정값 " + iga(match[1]) + " 기준 " + match[3] + " 이상" + unit + "에 못 미칩니다."
      : "측정값 " + iga(match[1]) + " 기준 " + match[3] + " 이하" + unit + "를 넘습니다.";
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
  get value() { return t("trace.field.value"); }, get unit() { return t("trace.field.unit"); }, get condition() { return t("trace.field.condition"); }, get numerator() { return t("trace.field.numerator"); }, get denominator() { return t("trace.field.denominator"); }, get result_revision() { return t("trace.field.resultRevision"); },
  get source_span_refs() { return t("trace.field.sourceRefs"); }, get raw_value() { return t("trace.field.rawValue"); }, get observed_at() { return t("trace.field.observedAt"); }, get criterion_id() { return t("trace.field.criterion"); }, get threshold() { return t("trace.field.threshold"); },
  get comparator() { return t("trace.field.comparator"); }, get required() { return t("trace.field.required"); }, get rounding() { return t("trace.field.rounding"); }, get measure() { return t("trace.field.measure"); }, get rule_revision() { return t("trace.field.ruleRevision"); }, get policy() { return t("trace.field.policy"); },
};
const shown = (value: string | null) => (value === null || value === "" ? t("trace.field.empty") : value);

const conditionKeys: Record<string, {name: MessageKey; values: Record<string, MessageKey>}> = {
  weather: {name: "trace.condition.weather", values: {dry: "trace.condition.dry", rain: "trace.condition.rain", fog: "trace.condition.fog"}},
};
/** A condition the user wrote as a key and a value, in plain words. A key or value this screen does not know is shown as written. */
export function describeCondition(key: string, value: string, locale: Locale = getLocale()): string {
  const known = conditionKeys[key];
  if (!known) return `${key} = ${value}`;
  return `${t(known.name, locale)}: ${known.values[value] ? t(known.values[value], locale) : value}`;
}
export function describeConditionText(text: string, locale: Locale = getLocale()): string {
  const at = text.indexOf("=");
  return at < 0 ? text : describeCondition(text.slice(0, at).trim(), text.slice(at + 1).trim(), locale);
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
      if (change.before !== null) return message("traceResultRemoved", name);
      const result = lookup?.results.find(item => item.result_id === name);
      return result ? message("traceResultAdded", name, ` (${resultText(result)} · ${describeConditionText(result.condition)})`) : message("traceResultAdded", name, "");
    }
    const [before, after] = pair();
    return message("traceResultChanged", name, fieldNames[change.field] ?? t("trace.field.content"), shown(before), shown(after));
  }
  if (change.kind === "RULE") {
    if (change.field === "*") {
      if (change.before !== null) return t("trace.change.ruleRemoved");
      const rule = lookup?.rules.find(item => item.rule_id === name);
      return rule ? message("traceRuleAdded", ruleText(rule), describeConditionText(rule.condition)) : t("trace.change.ruleAdded");
    }
    const [before, after] = pair();
    return message("traceRuleChanged", fieldNames[change.field] ?? t("trace.field.content"), shown(before), shown(after));
  }
  if (change.kind === "LINK") return change.before === null ? message("traceLinkAdded", name) : message("traceLinkRemoved", name);
  return t("trace.change.policy");
}

export const verdictChangeSentence = (change: VerdictChange, titleOf: (id: string) => string) =>
  `${titleOf(change.subject_id)}: ${change.before === null ? t("trace.change.initial") : verdictLook(change.subject_kind, change.before).text} → ${verdictLook(change.subject_kind, change.after).text}`;

export const resultReasonText = (reason: string) =>
  reason === "SUPERSEDED_BY_LATER_RESULT" ? t("trace.result.superseded") : reason === "CONDITION_MISMATCH" ? t("trace.result.condition") : t("trace.result.excluded");

const lossTexts: Record<string, string> = {
  get evidence_text() { return t("trace.loss.evidence"); },
  get verdict_history() { return t("trace.loss.history"); },
  get confirmations() { return t("trace.loss.confirmation"); },
  get selection_policy() { return t("trace.loss.policy"); },
  get pending_changes() { return t("trace.loss.pending"); },
  get closures() { return t("trace.loss.closures"); },
};
export function lossText(line: string) {
  const key = line.split(":")[0];
  if (lossTexts[key]) return lossTexts[key];
  return line.startsWith("column ignored:") ? message("traceIgnoredColumn", line.slice(line.indexOf(":") + 1).trim()) : line;
}

export type IssueText = { message: string; next: string };
const where = (issue: ImportIssue) => (issue.row ? message("traceRow", issue.row) : "");
const ref = (issue: ImportIssue) => (issue.ref ? ` ${issue.ref}` : "");

export function issueText(issue: ImportIssue): IssueText {
  const table: Record<string, () => IssueText> = {
    STALE_BASE: () => ({ get message() { return t("trace.import.stale"); }, get next() { return t("trace.import.reexport"); } }),
    BASE_DIGEST_MISSING: () => ({ get message() { return t("trace.import.noBase"); }, get next() { return t("trace.import.keepHeaders"); } }),
    BASE_DIGEST_INCONSISTENT: () => ({ get message() { return t("trace.import.mixedBase"); }, get next() { return t("trace.import.oneFile"); } }),
    MODE_CREATE_NEEDS_EMPTY_PROJECT: () => ({ get message() { return t("trace.import.exists"); }, get next() { return t("trace.import.chooseUpdate"); } }),
    UPDATE_NEEDS_EXISTING_TRACE: () => ({ get message() { return t("trace.import.noTable"); }, get next() { return t("trace.import.chooseCreate"); } }),
    DUPLICATE_ID_IN_FILE: () => ({ message: message("traceDuplicate", ref(issue), where(issue)), get next() { return t("trace.import.deduplicate"); } }),
    ROW_INVALID: () => ({ message: message("traceInvalidRow", issue.row ?? "?", ref(issue)), get next() { return t("trace.import.validFields"); } }),
    ITEM_KEY_CHANGED: () => ({ message: message("traceKeyChanged", ref(issue), where(issue)), get next() { return t("trace.import.keepKey"); } }),
    DELETE_TARGET_NOT_FOUND: () => ({ message: message("traceDeleteMissing", ref(issue), where(issue)), get next() { return t("trace.import.checkDelete"); } }),
    DELETE_BLOCKED_BY_LINK: () => ({ message: message("traceDeleteLinks", ref(issue)), get next() { return t("trace.import.deleteLinks"); } }),
    DELETE_BLOCKED_BY_RULE: () => ({ message: message("traceDeleteRule", ref(issue)), get next() { return t("trace.import.deleteRules"); } }),
    DELETE_BLOCKED_BY_RESULT: () => ({ message: message("traceDeleteResult", ref(issue)), get next() { return t("trace.import.deleteResults"); } }),
    DELETE_AND_UPSERT_SAME_TARGET: () => ({ message: message("traceDeleteAndUpdate", ref(issue), where(issue)), get next() { return t("trace.import.chooseOne"); } }),
    DELETE_NOT_ALLOWED_IN_CREATE: () => ({ get message() { return t("trace.import.noDeleteCreate"); }, get next() { return t("trace.import.removeDelete"); } }),
    DELETE_ROW_INCOMPLETE: () => ({ message: message("traceDeleteIncomplete", where(issue)), get next() { return t("trace.import.deleteIdentity"); } }),
    EXCEL_ID_SUSPECTED: () => ({ message: message("traceSpreadsheetId", ref(issue), where(issue), issue.detail ?? ""), get next() { return t("trace.excelFix"); } }),
    GRAPH_INVALID: () => ({ get message() { return t("trace.invalidGraph"); }, get next() { return t("trace.checkLinks"); } }),
    HEADER_MISSING_COLUMNS: () => ({ get message() { return t("trace.import.headerMissing"); }, get next() { return t("trace.import.useExportHeader"); } }),
    HEADER_DUPLICATE_COLUMN: () => ({ get message() { return t("trace.import.headerDuplicate"); }, get next() { return t("trace.import.removeColumn"); } }),
    FORMAT_UNSUPPORTED: () => ({ message: message("traceFormatUnsupported", where(issue)), get next() { return t("trace.import.useExport"); } }),
    ROW_TYPE_UNKNOWN: () => ({ message: message("traceUnknownRowType", where(issue)), get next() { return t("trace.import.allowedTypes"); } }),
    ID_MISSING: () => ({ message: message("traceIdMissing", where(issue)), get next() { return t("trace.import.fillId"); } }),
    CSV_EMPTY: () => ({ get message() { return t("trace.import.empty"); }, get next() { return t("trace.import.chooseNonempty"); } }),
    CSV_HAS_NO_ROWS: () => ({ get message() { return t("trace.import.noRows"); }, get next() { return t("trace.import.chooseNonempty"); } }),
    FILE_TOO_LARGE: () => ({ get message() { return t("trace.import.tooLarge"); }, get next() { return t("trace.import.split"); } }),
    TOO_MANY_ROWS: () => ({ get message() { return t("trace.import.tooManyRows"); }, get next() { return t("trace.import.split"); } }),
    CSV_UNREADABLE: () => ({ get message() { return t("trace.import.unreadable"); }, get next() { return t("trace.import.utf8"); } }),
  };
  return (table[issue.code] ?? (() => ({ get message() { return t("trace.import.problem"); }, get next() { return t("trace.import.checkDetails"); } })))();
}

/** An apply that the server refused, in words and with what to do next. */
export function applyFailureText(error: unknown): IssueText {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("TRACE_IMPORT_PREVIEW_STALE")) return { get message() { return t("trace.apply.stale"); }, get next() { return t("trace.apply.previewAgain"); } };
  if (message.includes("TRACE_IMPORT_INPUT_CHANGED")) return { get message() { return t("trace.apply.fileChanged"); }, get next() { return t("trace.apply.previewAgain"); } };
  if (message.includes("TRACE_IMPORT_NOT_APPLICABLE")) return { get message() { return t("trace.apply.unresolved"); }, get next() { return t("trace.apply.fixPreview"); } };
  if (message.includes("TRACE_REVISION_CONFLICT")) return { get message() { return t("trace.apply.conflict"); }, get next() { return t("trace.apply.reload"); } };
  if (message.includes("closing or archived")) return { get message() { return t("trace.apply.closed"); }, get next() { return t("trace.apply.checkProject"); } };
  return { get message() { return t("trace.apply.failed"); }, get next() { return t("trace.apply.retry"); } };
}
