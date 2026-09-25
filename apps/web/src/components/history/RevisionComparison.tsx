import { Callout } from "@blueprintjs/core";
import type { RevisionComparison as Comparison } from "../../api/historyModels";
import { describeValue, groupLabels } from "./historyPresentation";
import { Disclosure } from "../Disclosure";

export function RevisionComparison({ comparison, beforeLabel = "선택한 기록", afterLabel = "현재", title = "선택한 기록과 현재의 차이" }: {
  comparison: Comparison; beforeLabel?: string; afterLabel?: string; title?: string;
}) {
  const groups = new Map<string, Comparison["changes"]>();
  for (const change of comparison.changes) {
    const key = Object.hasOwn(groupLabels, change.group) ? change.group : "OTHER";
    groups.set(key, [...(groups.get(key) ?? []), change]);
  }
  return <section className="history-comparison" aria-label="변경 비교">
    <h3>{title}</h3>
    <p className="history-help">저장된 두 버전의 내용입니다. 변경 이유가 기록되지 않은 경우 추정하지 않습니다.</p>
    {comparison.coverage !== "COMPLETE" && <Callout compact intent="warning">일부 변경의 의미를 분류하지 못했습니다. 확인 가능한 차이만 표시합니다.</Callout>}
    {comparison.changes.length === 0 ? <p className="history-empty-copy">확인한 내용은 같습니다.</p> : [...groups].map(([kind, changes]) =>
      <section className="history-change-group" key={kind}><h4>{groupLabels[kind]}</h4>
        {changes.map((change, index) => <article className="history-change" key={`${change.path}:${index}`}>
          <h5>{change.label || "내용 변경"}</h5>
          <div className="history-diff-values"><div><span>{beforeLabel}</span><pre>{describeValue(change.before, change.beforeMissing)}</pre></div>
            <div><span>{afterLabel}</span><pre>{describeValue(change.after, change.afterMissing)}</pre></div></div>
          <Disclosure label="변경 위치"><code>{change.path}</code></Disclosure>
        </article>)}
      </section>)}
    {comparison.reasons.length > 0 && <Disclosure className="history-technical" label="비교 진단"><ul>{comparison.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></Disclosure>}
  </section>;
}
