import catalog from "../../../../schemas/protocol/public-method-catalog.json";
import { readQueries } from "./rpcClient";

export type Capability = (typeof catalog.methods)[number];
export const capabilityCatalog = catalog;
export const capabilities = catalog.methods;
export const namespaces = [...new Set(capabilities.map(method => method.namespace))].sort();
export const namespaceLabels: Record<string, string> = {
  project: "프로젝트 · 자료 · 접근", thread: "작업 · 요청", investigation: "자료 탐색", evidence: "근거 · 주장",
  object: "판단 객체", criteria: "평가기준", hypothesis: "가설 · 검증", action: "행동 · 권한",
  execution: "실행 · 복구", revision: "이력 · 분기 · 복원", outcome: "관찰 결과", memory: "프로젝트 기억",
  improvement: "개선 · 평가", receipt: "영수증 · 추적", closure: "마감 · 보존", export: "내보내기",
  operation: "비동기 제어", model: "모델 설정", workspace: "워크스페이스 설정",
  projectpack: "검증 예제", field: "현장 평가",
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
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("입력은 JSON 객체여야 합니다.");
  const input = value as Record<string, unknown>;
  if (!projectId || input.project_id !== projectId) throw new Error("선택한 프로젝트와 project_id가 일치해야 합니다.");
  return input;
}
