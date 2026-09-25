import { Button, Callout, TextArea } from "@blueprintjs/core";
import { ModelSettings } from "./ModelSettings";
import { ResearchProgress, UsageSummary } from "./ResearchProgress";
import { useCitationGate } from "./SourceCitationGate";
import type { useResearchSession } from "./useResearchSession";

export function ResearchComposer({session,projectId,threadId,epoch,onAttach,hosted=false}: {
  session:ReturnType<typeof useResearchSession>;projectId:string;threadId:string;epoch:number;onAttach:()=>void;hosted?:boolean;
}) {
  const citation=useCitationGate(projectId);
  const submit = async () => {
    const refs=[...session.problem.matchAll(/artifact:[A-Za-z0-9._:-]+/g)].map(item=>item[0]);
    for (const artifactId of refs) {
      const result=await citation.ask(artifactId);
      if(result.status!=="ELIGIBLE") return;
    }
    session.send();
  };
  return <div className="composer-dock">
    {session.research.data?.value && <ResearchProgress status={session.research.data.value}/>}
    {citation.dialog}{citation.message&&<Callout compact>{citation.message}</Callout>}<form className="prompt-composer" onSubmit={event=>{event.preventDefault();void submit();}}>
      <label className="composer-label" htmlFor="live-problem">현재 막힌 문제</label>
      <TextArea id="live-problem" fill rows={2} value={session.problem} onChange={event=>session.setProblem(event.target.value)}
        placeholder={threadId?"추가 질문, 새 근거, 바뀐 조건을 적어주세요…":"어떤 문제를 해결하고 싶으신가요?"}/>
      <div className="composer-tools"><Button minimal icon="paperclip" onClick={onAttach}>자료 연결</Button>
        {hosted ? null : <ModelSettings compact key={`${projectId}:${threadId||"new"}`} projectId={projectId} threadId={threadId||undefined}
          selection={session.modelSelection} onSelect={session.chooseModel} onSaved={()=>session.clearModel(session.renderedRevision,epoch)}/>}
        <Button icon="arrow-up" intent="primary" type="submit" aria-label={threadId?"지시 추가":"첫 조사 시작"} disabled={!session.problem.trim()} loading={session.submit.isPending}>{threadId?"지시 추가":"첫 조사 시작"}</Button>
      </div>
      {session.submit.error&&<Callout compact intent="danger" role="alert">{session.submit.error.message}</Callout>}
    </form>
    <UsageSummary status={session.research.data?.value}/>
  </div>;
}
