import { Button, Callout } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ResearchStatus } from "../api/research";
import { composerActivity } from "../api/researchProgress";
import { rpc } from "../api/rpcClient";

export function ResearchProgress({ status }: { status: ResearchStatus }) {
  const client = useQueryClient();
  const control = useMutation({ mutationFn: (method: string) => rpc<Record<string, unknown>>(method,
    { project_id: status.project_id, thread_id: status.thread_id }, crypto.randomUUID()),
    onSuccess: () => Promise.all([client.invalidateQueries({queryKey:["research",status.project_id,status.thread_id]}), client.invalidateQueries({queryKey:["threads",status.project_id]})]),
  });
  const message = composerActivity(status);
  const paused = status.execution_state === "PAUSED";
  const running = status.operation_state === "RUNNING";
  return <div className="activity-disclosure" aria-label="현재 작업">
    {message && <p role="status"><span className={paused ? "activity-dot paused" : "activity-dot"}/>{message}</p>}
    <div className="activity-controls">
      {running && !paused && <Button small minimal icon="pause" disabled={control.isPending} onClick={()=>control.mutate("thread/pause")}>일시정지</Button>}
      {paused && <Button small minimal icon="play" disabled={control.isPending} onClick={()=>control.mutate("thread/resume")}>이어서 진행</Button>}
      {running && <Button small minimal icon="stop" disabled={control.isPending} onClick={()=>{if(window.confirm("이 작업을 중단할까요? 이미 진행한 외부 실행의 종료 여부는 별도로 확인됩니다."))control.mutate("thread/stop");}}>중단 요청</Button>}
    </div>
    {control.error && <Callout compact intent="danger">{control.error.message}</Callout>}
  </div>;
}
export function UsageSummary({status}: {status?: Pick<ResearchStatus, "usage">}) {
  const usage=status?.usage;
  const number=(value:number|null|undefined)=>value == null ? "미확인" : value.toLocaleString();
  return <span className="usage-summary" title={`현재 작업의 제공자 보고값: 입력 ${number(usage?.input_tokens)}, 출력 ${number(usage?.output_tokens)}. 적용 가능한 단가·비용 근거가 없어 추정 비용은 미확인입니다. 계정 잔여량이나 추가 청구액이 아닙니다.`}>
    사용 토큰 {number(usage?.total_tokens)}{usage?.state === "PARTIAL" && " (부분 관측)"} · 추정 비용 미확인
  </span>;
}
