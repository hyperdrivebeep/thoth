import { Switch } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";

type CallSettings = { auto_retry_interrupted_model_call: boolean; settings_digest: string | null };

export const autoRetryHelp = "모델 호출이 중간에 끊기면 그 호출만 최대 2번 자동으로 다시 시도합니다. 토큰이 더 쓰입니다. 끄면 끊긴 결과 카드의 \"이어서 조사\"로 다시 할 수 있습니다.";

/** The project's switch for sending a cut-off model call again (at most twice). It shows what the server says. */
export function AutoRetrySwitch({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["model-call-settings", projectId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => rpc<CallSettings>("model/callSettings/read", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const change = useMutation({
    mutationFn: (enabled: boolean) => rpc<CallSettings>("model/callSettings/update", {
      project_id: projectId, auto_retry_interrupted_model_call: enabled, expected_digest: query.data?.value.settings_digest ?? null,
    }, crypto.randomUUID()),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  if (query.isPending) return null;
  if (query.error || !query.data) return <p className="muted" role="status">자동 재시도 설정을 읽지 못했습니다.</p>;
  return <div className="auto-retry-switch" data-auto-retry-switch>
    <Switch checked={query.data.value.auto_retry_interrupted_model_call} disabled={change.isPending} label="끊긴 모델 호출 자동 재시도"
      onChange={event => change.mutate(event.currentTarget.checked)}/>
    <small className="muted">{autoRetryHelp}</small>
    {change.error && <small role="alert">설정을 바꾸지 못했습니다. 화면을 다시 읽었으니 다시 시도해 주세요.</small>}
  </div>;
}

