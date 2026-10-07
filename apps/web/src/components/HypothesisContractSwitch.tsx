import { Switch } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";

type CallSettings = { auto_retry_interrupted_model_call: boolean; hypothesis_contract_v3: boolean; settings_digest: string | null };

export const hypothesisContractHelp = "켜면 가설을 만드는 같은 호출에서 각 시험이 가설마다 어떤 결과를 낼지와 이 가설을 버릴 조건도 함께 받아, 시험 순서를 더 정확히 셉니다. 모델을 더 부르지는 않습니다. 답이 조금 길어질 수 있고, 모델이 낸 것은 미확정 제안으로만 보입니다. 기본은 꺼짐입니다.";

/** A project's switch for asking the generator for expected results and refutation conditions (off unless turned on). It shows what the server says. */
export function HypothesisContractSwitch({ projectId }: { projectId: string }) {
  const client = useQueryClient();
  const key = ["model-call-settings", projectId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => rpc<CallSettings>("model/callSettings/read", { project_id: projectId }, crypto.randomUUID(), signal),
  });
  const change = useMutation({
    mutationFn: (enabled: boolean) => rpc<CallSettings>("model/callSettings/update", {
      project_id: projectId, hypothesis_contract_v3: enabled, expected_digest: query.data?.value.settings_digest ?? null,
    }, crypto.randomUUID()),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  });
  if (query.isPending) return null;
  if (query.error || !query.data) return <p className="muted" role="status">가설 생성 설정을 읽지 못했습니다.</p>;
  return <div className="hypothesis-contract-switch">
    <Switch checked={query.data.value.hypothesis_contract_v3 === true} disabled={change.isPending} label="가설 만들 때 예상 결과와 기각 조건도 함께 받기(고급)"
      onChange={event => change.mutate(event.currentTarget.checked)}/>
    <small className="muted">{hypothesisContractHelp}</small>
    {change.error && <small role="alert">설정을 바꾸지 못했습니다. 화면을 다시 읽었으니 다시 시도해 주세요.</small>}
  </div>;
}
