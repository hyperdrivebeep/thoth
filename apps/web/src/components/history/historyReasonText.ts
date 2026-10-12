import { historyReasons as reasons } from "../../i18n/historyReasons";
import { getLocale } from "../../i18n/locale";
/** Plain-language text for the reason codes the research-history screens receive. Raw codes stay in the technical details. */



const CODE_SHAPE = /^[A-Z][A-Z0-9_]+$/;

/** A sentence for one recorded reason. Text that is not a code (a gap or next step the research wrote) is shown as it is. */
export function describeHistoryReason(code: string): string {
  if (reasons[code]) return reasons[code];
  return CODE_SHAPE.test(code) ? (getLocale() === "en" ? "Another reason was recorded." : "기록된 다른 이유가 있습니다.") : code;
}

/** Sentences for a list of reasons, each wording once, in the order given. */
export function describeHistoryReasons(codes: readonly string[]): string[] {
  return [...new Set(codes.map(describeHistoryReason))];
}

export const isKnownHistoryReason = (code: string) => code in reasons;
