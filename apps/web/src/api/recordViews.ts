import { t } from "../i18n/locale";
export type RecordView = { namespace: string; label: string; description: string; method: string | null; scope?: "thread" | "operation"; note?: string };
/** Dedicated read entrypoints; backend command schemas remain the authority. */
export const recordViews: RecordView[] = [
  {namespace:"project",get label() { return t("workspace.projects"); },get description() { return t("advanced.projectHint"); },method:"project/read"},
  {namespace:"thread",get label() { return t("advanced.task"); },get description() { return t("advanced.taskHint"); },method:"thread/read",scope:"thread"},
  {namespace:"investigation",get label() { return t("advanced.discovery"); },get description() { return t("advanced.discoveryHint"); },method:"investigation/list"},
  {namespace:"evidence",get label() { return t("history.evidence"); },get description() { return t("advanced.evidenceHint"); },method:"evidence/list"},
  {namespace:"object",get label() { return t("advanced.object"); },get description() { return t("advanced.objectHint"); },method:"object/list"},
  {namespace:"criteria",get label() { return t("record.criterion"); },get description() { return t("advanced.criteriaHint"); },method:"criteria/list"},
  {namespace:"hypothesis",get label() { return t("hypotheses.hypothesis"); },get description() { return t("advanced.hypothesisHint"); },method:"hypothesis/list"},
  {namespace:"action",get label() { return t("history.action"); },get description() { return t("advanced.actionHint"); },method:"action/list"},
  {namespace:"execution",get label() { return t("advanced.execution"); },get description() { return t("advanced.executionHint"); },method:"execution/list"},
  {namespace:"outcome",get label() { return t("advanced.outcomes"); },get description() { return t("advanced.outcomesHint"); },method:"outcome/list"},
  {namespace:"revision",get label() { return t("advanced.revisions"); },get description() { return t("advanced.revisionsHint"); },method:"revision/list"},
  {namespace:"memory",get label() { return t("result.memory"); },get description() { return t("advanced.memoryHint"); },method:"memory/list"},
  {namespace:"improvement",get label() { return t("advanced.improvement"); },get description() { return t("advanced.improvementHint"); },method:"improvement/list"},
  {namespace:"receipt",get label() { return t("advanced.receipt"); },get description() { return t("advanced.receiptHint"); },method:"receipt/list"},
  {namespace:"closure",get label() { return t("advanced.closure"); },get description() { return t("advanced.closureHint"); },method:"closure/list"},
  {namespace:"export",get label() { return t("advanced.export"); },get description() { return t("advanced.exportHint"); },method:"export/list"},
  {namespace:"operation",get label() { return t("advanced.async"); },get description() { return t("advanced.asyncHint"); },method:"operation/read",scope:"operation"},
  {namespace:"model",get label() { return t("advanced.models"); },get description() { return t("advanced.modelsHint"); },method:"model/settings/read"},
  {namespace:"trace",get label() { return t("workspace.trace"); },get description() { return t("advanced.traceHint"); },method:"trace/read"},
  {namespace:"workspace",get label() { return t("advanced.workspace"); },get description() { return t("advanced.workspaceHint"); },method:"workspace/setup/read"},
  {namespace:"projectpack",get label() { return t("advanced.examples"); },get description() { return t("advanced.examplesHint"); },method:"projectpack/list",get note() { return t("advanced.examplesNotice"); }},
  {namespace:"field",get label() { return t("advanced.field"); },get description() { return t("advanced.fieldHint"); },method:null,get note() { return t("advanced.fieldNotice"); }},
];

export const visibleRecordViews = recordViews.filter(
  (view) => view.namespace !== "projectpack" && view.namespace !== "field",
);
