import { message as formatMessage } from "../../i18n/formatMessages";
import { t } from "../../i18n/locale";
import { Tag } from "@blueprintjs/core";
import type { ChangeSummary } from "../../api/historyModels";
import { Disclosure } from "../Disclosure";

const flagLabels: Record<string, string> = {
  get NUMBER() { return t("changeSummary.number"); }, get NEGATION() { return t("changeSummary.negation"); }, get STATUS() { return t("changeSummary.state"); }, get CITATION() { return t("changeSummary.evidence"); }, get AUTHORITY() { return t("changeSummary.authority"); }, get TIME() { return t("changeSummary.time"); },
};
const brief = (text: string) => text.length > 48 ? `${text.slice(0, 47)}…` : text;

/** One line under a history entry: the first changed field and the flags that need a reader's attention. */
export function ChangeBrief({ summary }: { summary?: ChangeSummary | null }) {
  const first = summary?.lines[0];
  const other = summary?.other ?? 0;
  if (!summary || (!first && other === 0)) return null;
  const total = summary.lines.length + summary.more + (other > 0 ? 1 : 0);
  return <span className="history-change-brief">
    <span>{first ? `${first.label}: ${brief(first.before)} → ${brief(first.after)}` : formatMessage("otherChanges", other)}{total > 1 ? formatMessage("moreChanges", total-1) : ""}</span>
    {summary.flags.map(flag => <Tag key={flag} minimal intent="warning">{flagLabels[flag] ?? flag}</Tag>)}
  </span>;
}

/** What this version changed from its parent. Text is open by itself when a change must be read as text. */
export function ChangeDetail({ summary }: { summary?: ChangeSummary | null }) {
  const other = summary?.other ?? 0;
  if (!summary || (summary.lines.length === 0 && other === 0)) return null;
  return <section className="history-change-summary" aria-label={t("changeSummary.title")}>
    <h3>{t("changeSummary.title")}</h3>
    {summary.flags.length > 0 && <p className="history-help">{summary.flags.map(flag => flagLabels[flag] ?? flag).join(" · ")}{" "}{t("changeSummary.notice")}</p>}
    <Disclosure label={t("changeSummary.original")} defaultOpen={summary.text_diff_recommended}>
      {summary.lines.map((line, index) => <article className="history-change" key={index}>
        <h5>{line.label}</h5>
        <div className="history-diff-values"><div><span>{t("changeSummary.before")}</span><pre>{line.before}</pre></div><div><span>{t("changeSummary.after")}</span><pre>{line.after}</pre></div></div>
      </article>)}
      {summary.more > 0 && <p className="history-help">{formatMessage("additionalChanges", summary.more)}</p>}
    </Disclosure>
    {other > 0 && <p className="history-help">{formatMessage("otherItemsChanged", other)}</p>}
    {(summary.technical ?? []).length > 0 && <Disclosure label={t("oauth.technical")} defaultOpen={false}>
      {(summary.technical ?? []).map((line, index) => <article className="history-change" key={index}>
        <h5>{line.label}</h5>
        <div className="history-diff-values"><div><span>{t("changeSummary.before")}</span><pre>{line.before}</pre></div><div><span>{t("changeSummary.after")}</span><pre>{line.after}</pre></div></div>
      </article>)}
    </Disclosure>}
  </section>;
}
