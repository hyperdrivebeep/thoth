import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { statusLabel } from "./statusLabels";

/** Plain words for a draft made from a test: it is a request being prepared, never an approval. */

const REASONS: Record<string, string> = {
  get POLICY_UNDEFINED() { return t("actionDraft.undeclared"); },
  get APPROVAL_REQUIRED() { return t("actionDraft.approvalRequired"); },
  get PROHIBITED() { return t("actionDraft.prohibited"); },
};
const openNotice = () => t("actionDraft.allowed");

// The rule's own answer, in plain words. A state or role this screen does not know is left out rather than shown as a code.
const POLICY: Record<string, string> = {
  get APPROVAL_REQUIRED() { return t("actionDraft.approval"); }, get POLICY_UNDEFINED() { return t("actionDraft.checkEffects"); }, get PROHIBITED() { return t("actionDraft.unavailable"); },
  get AUTO_ALLOWED() { return t("actionDraft.noSeparateApproval"); }, get PREAUTHORIZED() { return t("actionDraft.preauthorized"); },
};
const ROLE: Record<string, string> = {
  get "project-owner"() { return t("actionDraft.projectOwner"); }, get "safety-owner"() { return t("actionDraft.safetyOwner"); }, get "external-interface-owner"() { return t("actionDraft.integrationOwner"); },
  get "effect-owner"() { return t("actionDraft.effectsOwner"); }, get "institution-authority"() { return t("actionDraft.authority"); }, get "sandbox-owner"() { return t("actionDraft.sandboxOwner"); },
};

function ruleLine(action: Record<string, unknown>): string | null {
  const policy = typeof action.policy_state === "string" ? POLICY[action.policy_state] : undefined;
  if (!policy) return null;
  const roles = Array.isArray(action.required_roles) ? action.required_roles.map(role => ROLE[String(role)] ?? t("actionDraft.owner")) : [];
  return roles.length > 0 ? formatMessage("policyPeople", policy, [...new Set(roles)].join(", ")) : formatMessage("policyPeople", policy, null);
}

export function draftReadyLines(action: Record<string, unknown>): string[] {
  const tier = typeof action.risk_tier === "string" ? action.risk_tier : "";
  const policy = typeof action.policy_state === "string" ? action.policy_state : "";
  const rule = ruleLine(action);
  return [
    t("actionDraft.prepared"),
    formatMessage("riskTier", statusLabel(tier) ?? t("actionDraft.checkNeeded")),
    ...(rule ? [rule] : []),
    REASONS[policy] ?? openNotice(),
  ];
}

export function draftRefusal(message: string): string {
  return message.includes("HYPOTHESIS_BASIS_CHANGED")
    ? t("actionDraft.stale")
    : t("actionDraft.failed");
}
