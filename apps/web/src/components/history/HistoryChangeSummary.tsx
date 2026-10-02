import { Tag } from "@blueprintjs/core";
import type { ChangeSummary } from "../../api/historyModels";
import { Disclosure } from "../Disclosure";

const flagLabels: Record<string, string> = {
  NUMBER: "숫자 변경", NEGATION: "부정 표현 변경", STATUS: "상태 변경", CITATION: "인용·근거 변경", AUTHORITY: "출처 권위 변경", TIME: "시점 변경",
};
const brief = (text: string) => text.length > 48 ? `${text.slice(0, 47)}…` : text;

/** One line under a history entry: the first changed field and the flags that need a reader's attention. */
export function ChangeBrief({ summary }: { summary?: ChangeSummary | null }) {
  const first = summary?.lines[0];
  const other = summary?.other ?? 0;
  if (!summary || (!first && other === 0)) return null;
  const total = summary.lines.length + summary.more + (other > 0 ? 1 : 0);
  return <span className="history-change-brief">
    <span>{first ? `${first.label}: ${brief(first.before)} → ${brief(first.after)}` : `그 밖의 항목 ${other}개 변경`}{total > 1 ? ` · 외 ${total - 1}개 변경` : ""}</span>
    {summary.flags.map(flag => <Tag key={flag} minimal intent="warning">{flagLabels[flag] ?? flag}</Tag>)}
  </span>;
}

/** What this version changed from its parent. Text is open by itself when a change must be read as text. */
export function ChangeDetail({ summary }: { summary?: ChangeSummary | null }) {
  const other = summary?.other ?? 0;
  if (!summary || (summary.lines.length === 0 && other === 0)) return null;
  return <section className="history-change-summary" aria-label="이 버전에서 바뀐 것">
    <h3>이 버전에서 바뀐 것</h3>
    {summary.flags.length > 0 && <p className="history-help">{summary.flags.map(flag => flagLabels[flag] ?? flag).join(" · ")} — 숫자·부정·상태·인용·권위·시점이 바뀐 항목은 원문을 함께 보여 줍니다.</p>}
    <Disclosure label="바뀐 내용 원문" defaultOpen={summary.text_diff_recommended}>
      {summary.lines.map((line, index) => <article className="history-change" key={index}>
        <h5>{line.label}</h5>
        <div className="history-diff-values"><div><span>이전</span><pre>{line.before}</pre></div><div><span>이후</span><pre>{line.after}</pre></div></div>
      </article>)}
      {summary.more > 0 && <p className="history-help">이 밖에 {summary.more}개 항목이 더 바뀌었습니다.</p>}
    </Disclosure>
    {other > 0 && <p className="history-help">그 밖의 항목 {other}개가 바뀌었습니다.</p>}
    {(summary.technical ?? []).length > 0 && <Disclosure label="기술 정보" defaultOpen={false}>
      {(summary.technical ?? []).map((line, index) => <article className="history-change" key={index}>
        <h5>{line.label}</h5>
        <div className="history-diff-values"><div><span>이전</span><pre>{line.before}</pre></div><div><span>이후</span><pre>{line.after}</pre></div></div>
      </article>)}
    </Disclosure>}
  </section>;
}
