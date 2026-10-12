import { message as formatMessage } from "../../i18n/formatMessages";
import { t } from "../../i18n/locale";
import { objectList, objectValue, stringValues, textValue } from "../../api/presentation";
import { Disclosure } from "../Disclosure";
import { ReadableList } from "../ReadableList";

const prose: Record<string, string> = {
  get statement() { return t("hypotheses.hypothesis"); }, get observed_problem() { return t("record.observedProblem"); }, get evidence_basis() { return t("record.evidenceBasis"); }, get uncertainty() { return t("record.uncertainty"); }, get interpretation() { return t("record.interpretation"); }, get resolution() { return t("record.conclusion"); },
  get primary_purpose() { return t("record.purpose"); }, get rationale() { return t("record.rationale"); }, get description() { return t("trace.field.content"); }, get summary() { return t("record.summary"); }, get reason() { return t("record.changeReason"); }, get text() { return t("record.content"); },
  get assertion() { return t("record.memoryContent"); }, get content_excerpt() { return t("record.memoryBasis"); }, get message() { return t("record.progress"); },
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
    if (key === "evidence_basis" && text === "MODEL_CANDIDATE") text = t("record.modelCandidate");
    return text ? [{ key, title, text }] : [];
  });
  const specification = readableText(body.specification);
  const expected = readableText(body.expected_observation_or_change);
  const gaps = [...stringValues(body.missing_items), ...stringValues(objectValue(body.assessment).missing_items), ...stringValues(body.quality_gaps), ...stringValues(body.limitations)];
  const hypotheses = objectList(objectValue(body.portfolio).hypotheses);
  const actions = objectList(objectValue(body.action_plan).alternatives);
  const members = [...new Set([...stringValues(body.hypothesis_refs), ...stringValues(body.action_refs), ...stringValues(body.selected_action_refs)])];
  const counterQuestions = stringValues(body.counterevidence_queries);
  const dimensions = ([['scope_identity', t("record.reviewTarget")], ['criterion_authority', t("record.criterion")], ['evidence_coverage', t("record.sufficiency")], ['comparability', t("record.comparability")]] as const)
    .flatMap(([key, label]) => { const reason = textValue(objectValue(body[key]).reason); return reason ? [{ key, label, reason }] : []; });
  const steps = objectList(body.steps);
  const hasContent = paragraphs.length || specification || expected || gaps.length || members.length || steps.length || hypotheses.length || actions.length || dimensions.length || counterQuestions.length
    || [body.answer, body.authored_text, body.effective_question].some(value => typeof value === "string");
  return <div className="history-content">
    {paragraphs.map(item => <section key={item.key}><h3>{item.title}</h3><p>{item.text}</p></section>)}
    {specification && <section><h3>{t("record.proposedAction")}</h3><p>{specification}</p></section>}
    {expected && <section><h3>{t("record.expectedChange")}</h3><p>{expected}</p></section>}
    {dimensions.map(item => <section key={item.key}><h3>{item.label}</h3><p>{item.reason}</p></section>)}
    {counterQuestions.length > 0 && <section><h3>{t("record.counterQuestions")}</h3><ul>{counterQuestions.map((question, i) => <li key={i}>{question}</li>)}</ul></section>}
    {hypotheses.length > 0 && <section><h3>{t("record.pastHypotheses")}</h3><ul>{hypotheses.map((item, i) => <li key={i}>{textValue(item.statement)}</li>)}</ul></section>}
    {actions.length > 0 && <section><h3>{t("record.pastActions")}</h3><ul>{actions.map((item, i) => <li key={i}>{readableText(item.specification)}</li>)}</ul></section>}
    {gaps.length > 0 && <section><h3>{t("result.openChecks")}</h3><ReadableList items={gaps}/></section>}
    {steps.length > 0 && <section><h3>{t("record.actionPlan")}</h3><ol>{steps.map((step, i) => <li key={i}>{readableText(step) || textValue(step.label) || t("record.detailsInRecord")}</li>)}</ol></section>}
    {members.length > 0 && <section><h3>{t("record.membership")}</h3><p>{formatMessage("membershipNotice", members.length)}</p><Disclosure label={t("record.linkIds")}><ul>{members.map(ref => <li key={ref}><code>{ref}</code></li>)}</ul></Disclosure></section>}
    {!hasContent && <p className="history-help">{t("record.noDescription")}</p>}
  </div>;
}
