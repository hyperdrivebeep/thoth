import { statusLabel } from "./statusLabels";

/** Plain words for a draft made from a test: it is a request being prepared, never an approval. */

const REASONS: Record<string, string> = {
  POLICY_UNDEFINED: "효과를 아직 선언하지 않아 보호된 행동으로 분류했습니다. 효과를 선언해 고치기 전에는 사람의 판단이 필요합니다.",
  APPROVAL_REQUIRED: "밖으로 쓰거나 기준을 바꾸는 행동이라 사람의 승인이 필요합니다.",
  PROHIBITED: "금지된 효과가 선언되어 실행할 수 없는 행동입니다.",
};
const OPEN = "선언한 효과로는 별도의 승인 없이 쓸 수 있는 등급입니다.";

// The rule's own answer, in plain words. A state or role this screen does not know is left out rather than shown as a code.
const POLICY: Record<string, string> = {
  APPROVAL_REQUIRED: "승인 필요", POLICY_UNDEFINED: "효과를 확인해야 함", PROHIBITED: "실행할 수 없음",
  AUTO_ALLOWED: "별도 승인 없이 가능", PREAUTHORIZED: "미리 허용된 범위",
};
const ROLE: Record<string, string> = {
  "project-owner": "프로젝트 책임자", "safety-owner": "안전 책임자", "external-interface-owner": "외부 연동 책임자",
  "effect-owner": "효과 확인 책임자", "institution-authority": "기관 권한자", "sandbox-owner": "격리 환경 책임자",
};

function ruleLine(action: Record<string, unknown>): string | null {
  const policy = typeof action.policy_state === "string" ? POLICY[action.policy_state] : undefined;
  if (!policy) return null;
  const roles = Array.isArray(action.required_roles) ? action.required_roles.map(role => ROLE[String(role)] ?? "담당 책임자") : [];
  return roles.length > 0 ? `규칙상: ${policy} · 필요한 사람: ${[...new Set(roles)].join(", ")}` : `규칙상: ${policy}`;
}

export function draftReadyLines(action: Record<string, unknown>): string[] {
  const tier = typeof action.risk_tier === "string" ? action.risk_tier : "";
  const policy = typeof action.policy_state === "string" ? action.policy_state : "";
  const rule = ruleLine(action);
  return [
    "행동 요청을 준비했습니다(초안). 아직 승인이 아닙니다.",
    `위험 등급: ${statusLabel(tier) ?? "확인 필요"}`,
    ...(rule ? [rule] : []),
    REASONS[policy] ?? OPEN,
  ];
}

export function draftRefusal(message: string): string {
  return message.includes("HYPOTHESIS_BASIS_CHANGED")
    ? "이 가설은 이전 근거 기준이라 행동 요청을 만들 수 없습니다. 먼저 가설을 다시 확인해 주세요."
    : "행동 요청을 준비하지 못했습니다. 잠시 뒤 다시 시도해 주세요.";
}
