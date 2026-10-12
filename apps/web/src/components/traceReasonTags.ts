import { traceReasonText } from "../i18n/traceReasonResources";
import type { TraceView, Verdict } from "../api/trace";

/**
 * Why a held or failed line is held or failed, in a few short words. The tags come only from the verdict's
 * own computed reasons, its chosen and set-aside results, the links of the table and its currentness; a
 * code this file does not know gets no tag. They split the existing states and add none.
 * Not covered on purpose: access to the test, equipment configuration, and how near a miss is.
 */

export type ReasonTagId =
  | "UNTESTED" | "UNLINKED" | "UNIT" | "DENOMINATOR" | "VALUE" | "CONDITION_MISMATCH"
  | "NO_RULE" | "VALID_MISS" | "REQUIRED_INCOMPLETE" | "BASIS_CHANGED";

export type ReasonTag = { id: ReasonTagId; label: string; hint: string };

export const REASON_TAG_TEXT = traceReasonText;

const ORDER: ReasonTagId[] = [
  "UNTESTED", "UNLINKED", "CONDITION_MISMATCH", "NO_RULE", "UNIT", "DENOMINATOR", "VALUE", "VALID_MISS", "REQUIRED_INCOMPLETE", "BASIS_CHANGED",
];
const INCOMPLETE_REQUIREMENT = new Set(["HOLD_INCOMPLETE", "FAIL_WITH_INCOMPLETE_COVERAGE", "HOLD_NO_CRITERIA"]);
/**
 * Every reason code the server gives a verdict, and the tags it can lead to ("NO_TAG" when it is left untagged on purpose).
 * A requirement's per-criterion reasons ("<criterion>:<state>") carry no code; its tag comes from its state.
 * A test reads the server source and fails when a code is missing here.
 */
export const REASON_CODE_TAGS: Record<string, readonly ReasonTagId[] | "NO_TAG"> = {
  NO_RULE: ["NO_RULE"],
  NO_RESULT: ["UNTESTED", "UNLINKED", "CONDITION_MISMATCH"],
  THRESHOLD_NOT_MET: ["VALID_MISS"],
  UNIT_MISMATCH: ["UNIT"],
  DENOMINATOR_NOT_POSITIVE: ["DENOMINATOR"],
  VALUE_DISAGREES_WITH_COUNTS: ["DENOMINATOR"],
  VALUE_NOT_A_NUMBER: ["VALUE"],
  VALUE_MISSING: ["VALUE"],
  VALUE_OUT_OF_RANGE: ["VALUE"],
  NO_REQUIRED_CRITERIA: ["REQUIRED_INCOMPLETE"],
};
const CODE_TAG: Record<string, ReasonTagId> = {
  UNIT_MISMATCH: "UNIT",
  DENOMINATOR_NOT_POSITIVE: "DENOMINATOR", VALUE_DISAGREES_WITH_COUNTS: "DENOMINATOR",
  VALUE_NOT_A_NUMBER: "VALUE", VALUE_MISSING: "VALUE", VALUE_OUT_OF_RANGE: "VALUE",
};
const codeOf = (reason: string) => reason.split(":")[0];

export function reasonTags(kind: "CRITERION" | "REQUIREMENT", verdict: Verdict, view: Pick<TraceView, "links">): ReasonTag[] {
  const found = new Set<ReasonTagId>();
  const codes = verdict.reasons.computed.map(codeOf);
  if (kind === "CRITERION") {
    if (codes.includes("NO_RULE")) found.add("NO_RULE");
    if (codes.includes("NO_RESULT")) {
      const setAside = (verdict.selection?.excluded ?? []).some(item => item.reason === "CONDITION_MISMATCH");
      const tested = view.links.some(link => link.relation === "VERIFIED_BY" && link.from_id === verdict.subject_id);
      found.add(setAside ? "CONDITION_MISMATCH" : tested ? "UNTESTED" : "UNLINKED");
    }
    for (const code of codes) if (CODE_TAG[code]) found.add(CODE_TAG[code]);
    const unusable = codes.some(code => CODE_TAG[code]);
    if (verdict.state === "FAIL_COMPUTED" && codes.includes("THRESHOLD_NOT_MET") && !unusable) found.add("VALID_MISS");
  } else if (INCOMPLETE_REQUIREMENT.has(verdict.state) || codes.includes("NO_REQUIRED_CRITERIA")) {
    found.add("REQUIRED_INCOMPLETE");
  }
  if (verdict.currentness.state === "STALE_BASIS") found.add("BASIS_CHANGED");
  return ORDER.filter(id => found.has(id)).map(id => ({ id, ...REASON_TAG_TEXT[id] }));
}
