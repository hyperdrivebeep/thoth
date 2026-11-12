import type { ExecutionError, ResearchFailure } from "../api/research";
import { hasResearchContent, textValue } from "../api/presentation";

export type InterruptionInput = {
  state: string;
  result: Record<string, unknown> | null;
  error?: ExecutionError | null;
  failure?: ResearchFailure | null;
  terminalReason?: string | null;
};

/** A model call that stopped the research early; the key names the wording under "result.". */
export type CutOffKind = "slow" | "runaway" | "connectionError" | "deadline" | "responseTimeout";

export type InterruptionView = {
  /** The most specific reason code recorded for this result ("" when none). */
  code: string;
  cause: ResearchFailure | undefined;
  failed: boolean;
  cancelled: boolean;
  groundingHold: boolean;
  /** Anything other than a run that finished its bounded research: cancelled, failed, held or stopped by the model call. */
  interrupted: boolean;
  /** The failure happened while storing the result, not while researching. */
  publication: boolean;
  /** The result carries a readable answer or findings. */
  content: boolean;
  cutOffKind: CutOffKind | null;
  /** A cut-off model call that left no answer text at all. */
  cutOff: boolean;
  /** The result carries answer text (a partial draft counts). */
  hasAnswer: boolean;
  /** The connection was cut (stalled stream or per-call deadline) after an answer was saved. */
  savedBeforeCut: boolean;
};

const CUT_OFF: Array<[RegExp, CutOffKind]> = [
  [/OAUTH_STALLED_STREAM/, "slow"],
  [/OAUTH_RUNAWAY_OUTPUT/, "runaway"],
  [/OAUTH_TRANSPORT_FAILURE/, "connectionError"],
  [/OAUTH_DISPATCH_DEADLINE/, "deadline"],
  [/OAUTH_[A-Z_]*TIMEOUT|OAUTH_TRANSPORT_DEADLINE/, "responseTimeout"],
];

function cutOffKindOf(code: string): CutOffKind | null {
  return CUT_OFF.find(([pattern]) => pattern.test(code))?.[1] ?? null;
}

/** The reason code to explain: the recorded failure first, then the operation error, then the result's terminal reason. */
function reasonCode(
  { result, error, terminalReason }: InterruptionInput,
  cause: ResearchFailure | undefined,
): string {
  return (
    cause?.primary.reason_code ??
    error?.data?.reason_code ??
    terminalReason ??
    textValue(result?.terminal_reason)
  );
}

function hasAnswerText(result: Record<string, unknown> | null): boolean {
  return typeof result?.answer === "string" && result.answer.trim() !== "";
}

/** Exact codes only: the model call was cut off by our own stall or time rule, not ended by an error. */
const CUT_AFTER_ANSWER_CODES = new Set([
  "OAUTH_STALLED_STREAM_REMOTE_STOP_UNKNOWN",
  "OAUTH_DISPATCH_DEADLINE_REMOTE_STOP_UNKNOWN",
]);

/**
 * How a result ended, read from the operation state, the recorded failure and the terminal reason. The web does not
 * decide this on its own: a SUCCEEDED operation whose terminal reason is a stalled model call is still reported as
 * interrupted, because that is what the server recorded for it.
 */
export function interruptionView(input: InterruptionInput): InterruptionView {
  const { state, result, error, failure, terminalReason } = input;
  const failed = state === "FAILED";
  const cancelled = state === "CANCELLED";
  const cause = failure ?? error?.data?.failure;
  const code = reasonCode(input, cause);
  const groundingHold = code === "GROUNDING_PUBLICATION_BINDING_MISMATCH";
  const stoppedEarly = Boolean(terminalReason) && terminalReason !== "BOUNDED_RESEARCH_COMPLETE";
  const publication =
    cause?.primary.origin === "RESULT_PUBLICATION" ||
    cause?.secondary?.origin === "HOLD_PUBLICATION";
  const cutOffKind = cutOffKindOf(code);
  return {
    code,
    cause,
    failed,
    cancelled,
    groundingHold,
    interrupted: cancelled || failed || groundingHold || stoppedEarly,
    publication,
    content: hasResearchContent(result),
    cutOffKind,
    cutOff: cutOffKind !== null && !hasAnswerText(result),
    hasAnswer: hasAnswerText(result),
    savedBeforeCut: CUT_AFTER_ANSWER_CODES.has(code) && hasAnswerText(result),
  };
}
