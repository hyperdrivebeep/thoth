import { Button, Callout, Classes, Dialog, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { rpc, RpcError } from "../api/rpcClient";
import type { ExecutionHold } from "../api/research";

/** The words for a refusal of "execution/invalidate" (STALE_CHECKPOINT -32031, DOMAIN_REJECTED -32030). */
function refusal(error: unknown): string {
  if (error instanceof RpcError && error.code === -32031) return "그 사이에 실행 상태가 바뀌어 정리하지 못했습니다. 상태를 다시 읽었으니 화면을 확인한 뒤 다시 시도하세요.";
  if (error instanceof RpcError && error.code === -32030) return "정리할 수 없는 상태입니다. 이미 정리됐거나 그 시험의 결과가 확정됐을 수 있습니다. 상태를 다시 읽었습니다.";
  return "정리하지 못했습니다. 잠시 뒤 다시 시도하세요.";
}

function inputSentence(hold: ExecutionHold): string | null {
  const diff = hold.input_difference;
  if (!diff) return null;
  const previous = diff.previous_count ?? 0;
  const current = diff.current_count ?? 0;
  if (diff.same) return "시험에 쓴 입력 파일은 이전과 같습니다(" + current + "개).";
  return "시험에 쓴 입력 파일이 이전과 다릅니다. 이전 " + previous + "개, 지금 " + current + "개, 빠진 것 " + (diff.removed?.length ?? 0) + "개, 새로 들어온 것 " + (diff.added?.length ?? 0) + "개입니다.";
}

/** A closed-loop test that was held. When an earlier run is unsettled, the one way out is "정리하고 새로 실행": clear it with a reason; the question is then sent again by the person. */
export function ExecutionHoldNotice({ hold, projectId, causeRef, onCleared }: { hold: ExecutionHold; projectId: string; causeRef: string; onCleared?: () => void }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [cleared, setCleared] = useState(false);
  const sending = useRef(false); // set at once, so a second press before the screen updates sends nothing
  const clearable = hold.exits.includes("CLEAR_AND_RERUN") && hold.plan_execution_id !== null && hold.attempt_id !== null && hold.execution_revision !== null;
  const clear = useMutation({
    mutationFn: (why: string) => rpc("execution/invalidate", {
      project_id: projectId, plan_execution_id: hold.plan_execution_id, cause_revision_ref: causeRef, impact_refs: [],
      clear_pending_attempt_id: hold.attempt_id, expected_execution_revision: hold.execution_revision, reason: why, input_difference: hold.input_difference,
    }, crypto.randomUUID()),
    onSettled: () => { sending.current = false; },
    onSuccess: async () => {
      setCleared(true); setOpen(false); setReason(""); setMessage("");
      await client.invalidateQueries({ queryKey: ["research", projectId] });
      onCleared?.();
    },
    onError: async error => {
      setMessage(refusal(error));
      await client.invalidateQueries({ queryKey: ["research", projectId] });
    },
  });
  const submit = () => {
    if (sending.current) return;
    if (!reason.trim()) { setMessage("정리하는 이유를 적어 주세요."); return; }
    setMessage("");
    sending.current = true;
    clear.mutate(reason.trim());
  };
  const technical = [hold.reason_code, hold.plan_execution_id, hold.execution_revision !== null ? "revision " + hold.execution_revision : null, hold.attempt_id, hold.attempt_state,
    ...(hold.input_difference?.removed ?? []).map(item => "removed " + item), ...(hold.input_difference?.added ?? []).map(item => "added " + item)].filter(Boolean);
  const technicalInfo = technical.length > 0 && <details className="connection-tech"><summary>기술 정보</summary><small>{technical.join(" · ")}</small></details>;
  if (!clearable) {
    return <div className="result-notice" role="status">
      <p><strong>시험을 실행하지 않고 보류했습니다.</strong> 이번에는 격리 환경 시험이 실행되지 않았습니다. 자세한 이유는 기술 정보에서 볼 수 있습니다.</p>
      {technicalInfo}
    </div>;
  }
  const difference = inputSentence(hold);
  return <div className="result-notice" role="status">
    <p><strong>시험을 다시 실행하지 않았습니다.</strong> 같은 시험이 앞서 한 번 실행됐지만 그 결과가 아직 확정되지 않아서, 같은 일을 두 번 하지 않으려고 다시 실행하지 않았습니다.</p>
    {difference && <p>{difference}</p>}
    {cleared
      ? <p>정리했습니다. 같은 질문을 다시 보내면 새로 실행합니다.{onCleared ? " 질문은 입력창에 채워 두었습니다." : ""}</p>
      : <>
        <p>할 수 있는 일: 이전 실행을 정리(이유가 기록됩니다)하고 같은 질문을 다시 보내면, 새로 한 번 실행합니다. 이전 실행과 그 결과 기록은 지워지지 않고 그대로 남습니다.</p>
        <Button small icon="refresh" onClick={() => { setMessage(""); setOpen(true); }}>정리하고 새로 실행</Button>
        <small className="muted"> 누른다고 바로 실행되지는 않습니다. 정리한 뒤 질문을 직접 보내야 시작합니다.</small>
      </>}
    {technicalInfo}
    <Dialog isOpen={open} onClose={() => { if (!clear.isPending) setOpen(false); }} title="정리하고 새로 실행">
      <div className={Classes.DIALOG_BODY}>
        <p>앞서 실행된 시험의 확정되지 않은 결과를 정리합니다. 누가, 언제, 왜 정리했는지가 기록으로 남고, 이전 실행과 결과는 그대로 보존됩니다.</p>
        <label className="review-note">정리하는 이유 (필수)
          <TextArea fill rows={3} aria-label="정리하는 이유" value={reason} maxLength={2000} onChange={event => setReason(event.target.value)}/></label>
        <small className="muted">예: 입력 파일을 일부러 바꾼 뒤 다시 실행하려고</small>
        {message && <Callout compact intent="warning" role="alert">{message}</Callout>}
      </div>
      <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
        <Button disabled={clear.isPending} onClick={() => setOpen(false)}>닫기</Button>
        <Button intent="primary" disabled={clear.isPending} loading={clear.isPending} onClick={submit}>정리하기</Button>
      </div></div>
    </Dialog>
  </div>;
}
