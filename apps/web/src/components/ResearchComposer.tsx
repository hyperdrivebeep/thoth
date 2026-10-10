import { Button, Callout, TextArea } from "@blueprintjs/core";
import { ModelSettings } from "./ModelSettings";
import { ResearchProgress, UsageSummary } from "./ResearchProgress";
import { useCitationGate } from "./SourceCitationGate";
import type { useResearchSession } from "./useResearchSession";

export function ResearchComposer({session,projectId,threadId,epoch,onAttach,onOpenSettings,hosted=false,executionReady=true,readSuspended=false,consentMissing=false,modelConnected}: {
  session:ReturnType<typeof useResearchSession>;projectId:string;threadId:string;epoch:number;onAttach:()=>void;onOpenSettings?:(target?:"consent")=>void;
  hosted?:boolean;executionReady?:boolean;readSuspended?:boolean;consentMissing?:boolean;modelConnected?:boolean;
}) {
  const citation=useCitationGate(projectId);
  const submit = async () => {
    if (!executionReady || readSuspended || session.pendingBlocksNewInput) return;
    const refs=[...session.problem.matchAll(/artifact:[A-Za-z0-9._:-]+/g)].map(item=>item[0]);
    for (const artifactId of refs) {
      const result=await citation.ask(artifactId);
      if(result.status!=="ELIGIBLE") return;
    }
    session.send();
  };
  return <div className="composer-dock">
    {!readSuspended && session.research.data?.value && <ResearchProgress status={session.research.data.value}/>}
    {!hosted && session.pendingRead.kind === "PENDING" && <Callout compact intent="warning" role="status">{session.submit.isPending
      ? "원 요청의 접수 상태를 확인하는 중입니다. 초안은 유지합니다."
      : "이전 요청이 접수됐을 수 있습니다. 재시작 뒤 자동 재전송하지 않았습니다. 새 입력은 원 요청을 확인한 뒤 보내세요."}
      {!session.submit.isPending && <Button small minimal disabled={!executionReady || readSuspended} onClick={session.retryPending}>이전 요청 같은 key로 확인</Button>}</Callout>}
    {!hosted && session.pendingRead.kind === "INVALID" && <Callout compact intent="danger" role="alert">이전 요청의 복구 정보를 확인하지 못했습니다. 원 작업을 확인하기 전에는 새 key로 제출하지 않습니다.</Callout>}
    {!hosted && session.pendingStorageError && <Callout compact intent="warning" role="alert">{session.pendingStorageError}</Callout>}
    {readSuspended && <Callout compact intent="warning" role="status">접근 범위를 다시 확인하는 중입니다. 초안은 계속 쓸 수 있으며 확인이 끝나기 전에는 제출하지 않습니다.</Callout>}
    {!readSuspended && !hosted && !executionReady && <Callout compact intent="warning" role="status">저장된 연구는 계속 볼 수 있습니다. {blockedReason(consentMissing,modelConnected)}
      {onOpenSettings && (consentMissing && modelConnected!==false
        ? <Button small minimal onClick={()=>onOpenSettings("consent")}>동의 고르기</Button>
        : <Button small minimal onClick={()=>onOpenSettings()}>모델 연결 확인</Button>)}</Callout>}
    {!hosted && session.draftRestoreIssue && <Callout compact intent="warning" role="alert">{session.draftRestoreIssue === "CORRUPT"
      ? "이전 초안 저장 기록을 읽지 못했습니다. 기록을 자동 삭제하지 않았으며 새 초안을 저장하면 대체될 수 있습니다."
      : "이 브라우저의 초안 저장 기록을 읽지 못했습니다. 브라우저 저장소와 작업 공간 식별자를 확인하세요."}
      {" "}다른 주소·포트·브라우저 프로필 또는 작업 공간의 초안은 자동 병합되지 않습니다. 서버의 저장 연구는 별도로 읽습니다.</Callout>}
    {!hosted && session.draftSaveState !== "SAVED" && <Callout compact intent="warning" role="status">{session.draftSaveState === "TOO_LARGE"
      ? "초안이 브라우저 저장 한도를 넘었습니다. 짧게 줄이기 전에는 재시작 후 복원되지 않을 수 있습니다."
      : "이 브라우저에 초안을 저장하지 못했습니다. 페이지를 닫으면 사라질 수 있습니다."}</Callout>}
    {!readSuspended&&citation.dialog}{!readSuspended&&citation.message&&<Callout compact>{citation.message}</Callout>}<form className="prompt-composer" onSubmit={event=>{event.preventDefault();void submit();}}>
      <label className="composer-label" htmlFor="live-problem">현재 막힌 문제</label>
      <TextArea id="live-problem" fill rows={2} value={session.problem} onChange={event=>session.setProblem(event.target.value)}
        placeholder={threadId?"추가 질문, 새 근거, 바뀐 조건을 적어주세요…":"어떤 문제를 해결하고 싶으신가요?"}/>
      <div className="composer-tools"><Button minimal icon="paperclip" disabled={readSuspended} onClick={onAttach}>자료 연결</Button>
        {hosted || readSuspended ? null : <ModelSettings compact key={`${projectId}:${threadId||"new"}`} projectId={projectId} threadId={threadId||undefined}
          selection={session.modelSelection} onSelect={session.chooseModel} onSaved={()=>session.clearModel(session.renderedRevision,epoch)}/>}
        <Button icon="arrow-up" intent="primary" type="submit" aria-label={threadId?"지시 추가":"첫 조사 시작"} disabled={readSuspended || !executionReady || session.pendingBlocksNewInput || !session.problem.trim()} loading={session.submit.isPending}>
          {session.pendingRead.kind === "PENDING" ? "원 요청 확인" : threadId?"지시 추가":"첫 조사 시작"}</Button>
      </div>
      {session.submit.error&&<Callout compact intent="danger" role="alert">{session.submit.error.message}</Callout>}
    </form>
    {!readSuspended&&<UsageSummary status={session.research.data?.value}/>}
  </div>;
}

/** What actually blocks a new research: an empty internet consent, the model, both, or something the screen cannot name. */
function blockedReason(consentMissing: boolean, modelConnected: boolean | undefined): string {
  if (consentMissing) return modelConnected === false ? "새 연구를 실행하려면 모델 연결과 인터넷 사용 동의를 확인하세요." : "새 연구를 실행하려면 인터넷 사용 동의를 고르세요.";
  return modelConnected === false ? "새 연구를 실행하려면 모델 연결을 확인하세요." : "새 연구를 실행하려면 모델 연결과 초기 설정을 확인하세요.";
}
