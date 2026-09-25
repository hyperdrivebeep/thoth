import { objectList, objectValue, stringValues, textValue } from "../../api/presentation";
import { Disclosure } from "../Disclosure";

const prose: Record<string, string> = {
  statement: "가설", observed_problem: "관측한 문제", evidence_basis: "근거의 바탕", uncertainty: "남은 불확실성", interpretation: "관측 해석", resolution: "정리한 결론",
  primary_purpose: "목적", rationale: "판단 이유", description: "내용", summary: "요약", reason: "변경 이유", text: "기록한 내용",
  assertion: "기억한 내용", content_excerpt: "기억의 바탕", message: "저장한 진행 내용",
};

function readableText(value: unknown): string {
  if (typeof value === "string") return value;
  const object = objectValue(value);
  return [object.statement, object.description, object.text, object.specification, object.expected_information_value]
    .filter((item): item is string => typeof item === "string").join("\n");
}

export function HistoryContent({ content }: { content: Record<string, unknown> }) {
  const body = Object.keys(objectValue(content.value)).length ? objectValue(content.value) : content;
  const paragraphs = Object.entries(prose).flatMap(([key, title]) => {
    let text = readableText(body[key]);
    if (key === "evidence_basis" && text === "MODEL_CANDIDATE") text = "모델이 제안한 후보";
    return text ? [{ key, title, text }] : [];
  });
  const specification = readableText(body.specification);
  const expected = readableText(body.expected_observation_or_change);
  const gaps = [...stringValues(body.missing_items), ...stringValues(objectValue(body.assessment).missing_items), ...stringValues(body.quality_gaps), ...stringValues(body.limitations)];
  const hypotheses = objectList(objectValue(body.portfolio).hypotheses);
  const actions = objectList(objectValue(body.action_plan).alternatives);
  const members = [...new Set([...stringValues(body.hypothesis_refs), ...stringValues(body.action_refs), ...stringValues(body.selected_action_refs)])];
  const counterQuestions = stringValues(body.counterevidence_queries);
  const dimensions = ([['scope_identity', '검토 대상'], ['criterion_authority', '평가기준'], ['evidence_coverage', '근거 충분성'], ['comparability', '비교 가능성']] as const)
    .flatMap(([key, label]) => { const reason = textValue(objectValue(body[key]).reason); return reason ? [{ key, label, reason }] : []; });
  const steps = objectList(body.steps);
  const hasContent = paragraphs.length || specification || expected || gaps.length || members.length || steps.length || hypotheses.length || actions.length || dimensions.length || counterQuestions.length
    || [body.answer, body.authored_text, body.effective_question].some(value => typeof value === "string");
  return <div className="history-content">
    {paragraphs.map(item => <section key={item.key}><h3>{item.title}</h3><p>{item.text}</p></section>)}
    {specification && <section><h3>제안한 행동</h3><p>{specification}</p></section>}
    {expected && <section><h3>기대하는 관측·변화</h3><p>{expected}</p></section>}
    {dimensions.map(item => <section key={item.key}><h3>{item.label}</h3><p>{item.reason}</p></section>)}
    {counterQuestions.length > 0 && <section><h3>반대 근거를 확인할 질문</h3><ul>{counterQuestions.map((question, i) => <li key={i}>{question}</li>)}</ul></section>}
    {hypotheses.length > 0 && <section><h3>당시 검토한 가설</h3><ul>{hypotheses.map((item, i) => <li key={i}>{textValue(item.statement)}</li>)}</ul></section>}
    {actions.length > 0 && <section><h3>당시 제안한 행동</h3><ul>{actions.map((item, i) => <li key={i}>{readableText(item.specification)}</li>)}</ul></section>}
    {gaps.length > 0 && <section><h3>아직 확인할 내용</h3><ul>{gaps.map((gap, i) => <li key={i}>{gap}</li>)}</ul></section>}
    {steps.length > 0 && <section><h3>행동 계획</h3><ol>{steps.map((step, i) => <li key={i}>{readableText(step) || textValue(step.label) || "세부 내용은 저장 기록에서 확인할 수 있습니다."}</li>)}</ol></section>}
    {members.length > 0 && <section><h3>목록의 구성</h3><p>이 버전은 {members.length}개 항목의 연결을 포함합니다. 연결된 항목의 내용까지 같은 시점으로 복원되는 것은 아닙니다.</p><Disclosure label="연결 식별자"><ul>{members.map(ref => <li key={ref}><code>{ref}</code></li>)}</ul></Disclosure></section>}
    {!hasContent && <p className="history-help">이 기록에 표시할 설명이 없습니다. 저장된 값은 세부 기록에서 확인할 수 있습니다.</p>}
  </div>;
}
