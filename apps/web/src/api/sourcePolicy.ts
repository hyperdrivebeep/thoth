import { z } from "zod";

// Mirrors domain/enums.py and domain/resource_scope.py; source parity is tested.
export const authorityStates = ["UNCLASSIFIED","INFORMAL","OFFICIAL","APPROVED","SUPERSEDED","NOT_ADMISSIBLE"] as const;
export const cutoffStates = ["ELIGIBLE","AFTER_CUTOFF","UNKNOWN_TIME","PROHIBITED_CONTEXT"] as const;
export const authorityOptions = [
  {value:"UNCLASSIFIED",label:"권위 미분류"},{value:"INFORMAL",label:"비공식 참고자료"},
  {value:"OFFICIAL",label:"공식자료"},{value:"APPROVED",label:"승인본"},
  {value:"SUPERSEDED",label:"대체된 자료"},{value:"NOT_ADMISSIBLE",label:"판단 사용 불가"},
];
export const cutoffOptions = [
  {value:"ELIGIBLE",label:"기준시점 이전"},{value:"UNKNOWN_TIME",label:"시점 미확인"},
  {value:"AFTER_CUTOFF",label:"기준시점 이후"},{value:"PROHIBITED_CONTEXT",label:"사용 금지 맥락"},
];
export const sourceConnectSchema = z.object({
  project_id:z.string().min(1),relative_path:z.string().min(1),media_type:z.string().min(1),
  authority:z.enum(authorityStates),cutoff_state:z.enum(cutoffStates),security_class:z.literal("INTERNAL"),
  resource_scope:z.object({owner_kind:z.literal("PROJECT"),visibility:z.enum(["PROJECT_SHARED","EXPLICIT_GRANT"])}),
  version_label:z.string(),
});
