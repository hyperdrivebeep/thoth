import { t } from "../i18n/locale";
import { z } from "zod";

// Mirrors domain/enums.py and domain/resource_scope.py; source parity is tested.
export const authorityStates = ["UNCLASSIFIED","INFORMAL","OFFICIAL","APPROVED","SUPERSEDED","NOT_ADMISSIBLE"] as const;
export const cutoffStates = ["ELIGIBLE","AFTER_CUTOFF","UNKNOWN_TIME","PROHIBITED_CONTEXT"] as const;
export const authorityOptions = [
  {value:"UNCLASSIFIED",get label() { return t("sources.unclassified"); }},{value:"INFORMAL",get label() { return t("sources.informal"); }},
  {value:"OFFICIAL",get label() { return t("sources.official"); }},{value:"APPROVED",get label() { return t("sources.approved"); }},
  {value:"SUPERSEDED",get label() { return t("sources.superseded"); }},{value:"NOT_ADMISSIBLE",get label() { return t("sources.inadmissible"); }},
];
export const cutoffOptions = [
  {value:"ELIGIBLE",get label() { return t("sources.beforeCutoff"); }},{value:"UNKNOWN_TIME",get label() { return t("sources.timeUnknown"); }},
  {value:"AFTER_CUTOFF",get label() { return t("sources.afterCutoff"); }},{value:"PROHIBITED_CONTEXT",get label() { return t("sources.prohibited"); }},
];
export const sourceConnectSchema = z.object({
  project_id:z.string().min(1),relative_path:z.string().min(1),media_type:z.string().min(1),
  authority:z.enum(authorityStates),cutoff_state:z.enum(cutoffStates),security_class:z.literal("INTERNAL"),
  resource_scope:z.object({owner_kind:z.literal("PROJECT"),visibility:z.enum(["PROJECT_SHARED","EXPLICIT_GRANT"])}),
  version_label:z.string(),
});
