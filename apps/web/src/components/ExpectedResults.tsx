import { objectList, stringValues, textValue } from "../api/presentation";
import type { HypothesisRow } from "./hypothesisView";

/** What the generator said each hypothesis would give for one test (contract v3), in words. An unconfirmed suggestion; no probability or score. */
export function ExpectedResults({ test, rows }: { test: Record<string, unknown>; rows: HypothesisRow[] }) {
  const table = objectList(test.expected_by_hypothesis);
  if (table.length === 0) return null;
  return <details className="expected-results">
    <summary>각 가설의 예상 결과(AI 제안)</summary>
    <ul>{table.map((item, at) => {
      const index = rows.findIndex(row => row.id === textValue(item.hypothesis_id));
      const sources = stringValues(item.basis).length;
      const expected = textValue(item.expected);
      const note = sources > 0 ? " · 근거 " + sources + "개" : expected.trim() === "모름" ? "" : " · 근거 없는 예측";
      return <li key={at}>{"가설 " + (index >= 0 ? index + 1 : "?") + ": " + expected + note}</li>;
    })}</ul>
    <small className="muted">모델이 낸 제안이며 사람이 확인하기 전에는 미확정입니다. 같은 말은 같은 결과로 봅니다. 확률이나 점수가 아닙니다.</small>
  </details>;
}
