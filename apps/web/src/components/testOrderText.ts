import { messages } from "../i18n/messages";
import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { testOrderTitles as TITLES } from "../i18n/testOrderResources";
import type { GroupId, Ranked } from "./testRanking";

/** Plain words for the order of tests and actions. No score and no probability is ever shown; only whole-number counts of candidates. */

export const ORDER_NOTE = messages["testOrder.note"].ko;
export const orderNote = () => t("testOrder.note");


export const groupTitle = (group: GroupId, kind: "TEST" | "ACTION") => TITLES[group][kind === "TEST" ? "test" : "action"];
export const groupHint = (group: GroupId) => TITLES[group].hint;
export const EXCLUDED_TITLE = messages["testOrder.excluded"].ko;
export const excludedTitle = () => t("testOrder.excluded");
export const excludedTestsLine = (count: number) => message("orderExcluded", count);

/** What the test does to the candidates, in whole numbers only. */
export function reductionLine(entry: Ranked, candidates: number): string {
  if (!entry.counted) return t("testOrder.uncountable");
  if (entry.worst >= candidates) return t("testOrder.noReduction");
  return message("orderReduction", entry.worst);
}
