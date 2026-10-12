import { uiText } from "../i18n/locale";
import { t } from "../i18n/locale";
import type { HypothesisLink } from "../api/hypothesisLink";
import type { LessonItem } from "../api/lessons";
import { Tag } from "@blueprintjs/core";
import { LESSON_NOTE, lessonDisplay, withheldLine } from "./lessonText";
import { SAME_TAG } from "./sameHypothesisText";

/**
 * Earlier results of the same row in the same condition: the criterion, the unit, the condition and the rule revision all match.
 * Shown as a reference only. The lessons of the hypotheses already on screen are not repeated, and nothing here changes an order.
 */
export function LessonRecall({ links, lessons, shownIds }: { links: HypothesisLink[] | undefined; lessons: LessonItem[] | undefined; shownIds: string[] }) {
  const rows = (links ?? []).filter(item => shownIds.includes(item.hypothesis_id));
  const mine = (lessons ?? []).filter(item => rows.some(row => row.subject_kind === item.subject_kind && row.subject_id === item.subject_id)
    && !(item.hypothesis_id !== null && shownIds.includes(item.hypothesis_id)));
  const recalled = mine.filter(item => item.state === "SAME_CONDITION").sort((a, b) => b.created_at.localeCompare(a.created_at));
  const withheld = withheldLine(mine.filter(item => item.state === "STALE").length, mine.filter(item => item.state === "REFUTED").length);
  if (recalled.length === 0 && !withheld) return null;
  return <section className="detail-card lesson-recall" aria-label={t("lesson.title")}>
    <h3>{t("lesson.title")}</h3>
    <p className="muted">{uiText(LESSON_NOTE)}</p>
    {recalled.length > 0 && <ul>{recalled.map(item => {
      const shown = lessonDisplay(item);
      const sameAsShown = (item.same_hypothesis_ids ?? []).some(id => shownIds.includes(id));
      return <li key={item.lesson_id}>{sameAsShown && <><Tag minimal intent="primary">{uiText(SAME_TAG)}</Tag>{" "}</>}<strong>{shown.title}</strong>{shown.lines.map((line, index) => <div key={index}><small>{line}</small></div>)}</li>;
    })}</ul>}
    {withheld && <p className="muted">{withheld}</p>}
  </section>;
}
