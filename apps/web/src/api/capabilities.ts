import { UiError } from "../i18n/UiError";
import { t } from "../i18n/locale";
import catalog from "../../../../schemas/protocol/public-method-catalog.json";
import { readQueries } from "./rpcClient";

export type Capability = (typeof catalog.methods)[number];
export const capabilityCatalog = catalog;
export const capabilities = catalog.methods;
export const namespaces = [...new Set(capabilities.map(method => method.namespace))].sort();
export const namespaceLabels: Record<string, string> = {
  get project() { return t("advanced.projectGroup"); }, get thread() { return t("advanced.threadGroup"); }, get investigation() { return t("advanced.discoveryGroup"); }, get evidence() { return t("advanced.evidenceGroup"); },
  get object() { return t("advanced.object"); }, get criteria() { return t("record.criterion"); }, get hypothesis() { return t("advanced.hypothesisGroup"); }, get action() { return t("advanced.actionGroup"); },
  get execution() { return t("advanced.execution"); }, get revision() { return t("advanced.historyGroup"); }, get outcome() { return t("advanced.outcomes"); }, get memory() { return t("result.memory"); },
  get improvement() { return t("advanced.improvementGroup"); }, get receipt() { return t("advanced.receiptGroup"); }, get closure() { return t("advanced.closureGroup"); }, get export() { return t("advanced.export"); },
  get operation() { return t("advanced.async"); }, get model() { return t("model.settings"); }, get workspace() { return t("advanced.workspace"); },
  get projectpack() { return t("advanced.examples"); }, get field() { return t("advanced.field"); }, get trace() { return t("workspace.trace"); },
};

export function filterCapabilities(search: string, namespace = "all", surface = "all") {
  const tokens = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return capabilities.filter(method =>
    (namespace === "all" || method.namespace === namespace) &&
    (surface === "all" || method.surface === surface) &&
    tokens.every(token => `${method.name} ${method.canonical_owner} ${method.policy} ${namespaceLabels[method.namespace] ?? ""}`.toLowerCase().includes(token)),
  );
}

export function transportFor(method: string) {
  return readQueries.has(method) ? "/rpc/query" : "/rpc";
}

export function parseInvocation(text: string, projectId: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new UiError("api.jsonObject");
  const input = value as Record<string, unknown>;
  if (!projectId || input.project_id !== projectId) throw new UiError("api.projectMismatch");
  return input;
}
