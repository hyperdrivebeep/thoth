import { t } from "../../i18n/locale";
import { Callout } from "@blueprintjs/core";
import type { RevisionComparison as Comparison } from "../../api/historyModels";
import { describeValue, groupLabels } from "./historyPresentation";
import { Disclosure } from "../Disclosure";

export function RevisionComparison({ comparison, beforeLabel = t("revision.selected"), afterLabel = t("revision.current"), title = t("revision.difference") }: {
  comparison: Comparison; beforeLabel?: string; afterLabel?: string; title?: string;
}) {
  const groups = new Map<string, Comparison["changes"]>();
  for (const change of comparison.changes) {
    const key = Object.hasOwn(groupLabels, change.group) ? change.group : "OTHER";
    groups.set(key, [...(groups.get(key) ?? []), change]);
  }
  return <section className="history-comparison" aria-label={t("revision.compare")}>
    <h3>{title}</h3>
    <p className="history-help">{t("revision.notice")}</p>
    {comparison.coverage !== "COMPLETE" && <Callout compact intent="warning">{t("revision.partial")}</Callout>}
    {comparison.changes.length === 0 ? <p className="history-empty-copy">{t("revision.same")}</p> : [...groups].map(([kind, changes]) =>
      <section className="history-change-group" key={kind}><h4>{groupLabels[kind]}</h4>
        {changes.map((change, index) => <article className="history-change" key={`${change.path}:${index}`}>
          <h5>{change.label || t("revision.content")}</h5>
          <div className="history-diff-values"><div><span>{beforeLabel}</span><pre>{describeValue(change.before, change.beforeMissing)}</pre></div>
            <div><span>{afterLabel}</span><pre>{describeValue(change.after, change.afterMissing)}</pre></div></div>
          <Disclosure label={t("revision.location")}><code>{change.path}</code></Disclosure>
        </article>)}
      </section>)}
    {comparison.reasons.length > 0 && <Disclosure className="history-technical" label={t("revision.diagnostics")}><ul>{comparison.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></Disclosure>}
  </section>;
}
