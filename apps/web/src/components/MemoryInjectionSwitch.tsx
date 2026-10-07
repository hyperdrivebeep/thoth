import { Switch } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";

type MemorySettings = { memory_injection: boolean; query_expansion?: boolean; settings_digest: string | null };

export const memoryInjectionHelp = "끄면 저장된 기억과 정정은 그대로 두고, 앞으로의 조사에는 기억을 넣지 않습니다.";
export const queryExpansionLabel = "검색어 넓히기(조사마다 모델을 한 번 더 부릅니다)";
export const queryExpansionHelp = "질문과 다른 말로 저장된 기억도 찾도록 모델이 검색어를 덧붙입니다. 끄면 질문에 쓴 말로만 찾고, 저장된 기억은 그대로입니다.";

/** The project's switches: handing stored memory to an investigation, and widening its question. */
export function MemoryInjectionSwitch({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["memory-settings", projectId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => rpc<MemorySettings>("memory/settings/read", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const change = useMutation({
    mutationFn: (next: { injection?: boolean; expansion?: boolean }) => rpc<MemorySettings>("memory/settings/update", {
      project_id: projectId,
      memory_injection: next.injection ?? query.data?.value.memory_injection ?? true,
      // Only sent when the expansion switch itself changes; the project keeps its choice otherwise.
      ...(next.expansion === undefined ? {} : { query_expansion: next.expansion }),
      expected_digest: query.data?.value.settings_digest ?? null,
    }, crypto.randomUUID()),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  if (query.isPending) return null;
  if (query.error || !query.data) return <p className="muted" role="status">기억 사용 설정을 읽지 못했습니다.</p>;
  const enabled = query.data.value.memory_injection;
  const widening = query.data.value.query_expansion ?? true;
  return <div className="memory-switch" data-memory-switch>
    <Switch checked={enabled} disabled={change.isPending} label="조사에 기억 사용" onChange={event => change.mutate({ injection: event.currentTarget.checked })}/>
    <small className="muted">{memoryInjectionHelp}</small>
    <Switch checked={widening} disabled={change.isPending || !enabled} label={queryExpansionLabel}
      onChange={event => change.mutate({ expansion: event.currentTarget.checked })} data-query-expansion-switch/>
    <small className="muted">{queryExpansionHelp}</small>
    {change.error && <small role="alert">설정을 바꾸지 못했습니다. 화면을 다시 읽었으니 다시 시도해 주세요.</small>}
  </div>;
}
