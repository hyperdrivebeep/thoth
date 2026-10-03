import { Switch } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";

type MemorySettings = { memory_injection: boolean; settings_digest: string | null };

export const memoryInjectionHelp = "끄면 저장된 기억과 정정은 그대로 두고, 앞으로의 조사에는 기억을 넣지 않습니다.";

/** The project's on/off switch for handing stored memory to an investigation. */
export function MemoryInjectionSwitch({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["memory-settings", projectId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => rpc<MemorySettings>("memory/settings/read", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const change = useMutation({
    mutationFn: (enabled: boolean) => rpc<MemorySettings>("memory/settings/update", {
      project_id: projectId, memory_injection: enabled, expected_digest: query.data?.value.settings_digest ?? null,
    }, crypto.randomUUID()),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  if (query.isPending) return null;
  if (query.error || !query.data) return <p className="muted" role="status">기억 사용 설정을 읽지 못했습니다.</p>;
  const enabled = query.data.value.memory_injection;
  return <div className="memory-switch" data-memory-switch>
    <Switch checked={enabled} disabled={change.isPending} label="조사에 기억 사용" onChange={event => change.mutate(event.currentTarget.checked)}/>
    <small className="muted">{memoryInjectionHelp}</small>
    {change.error && <small role="alert">설정을 바꾸지 못했습니다. 화면을 다시 읽었으니 다시 시도해 주세요.</small>}
  </div>;
}
