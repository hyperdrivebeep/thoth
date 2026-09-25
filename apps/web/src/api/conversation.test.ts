import { afterEach, expect, it, vi } from "vitest";
import { readConversation, withCurrentCheckpoint, type ConversationTurn } from "./conversation";
import { rpc } from "./rpcClient";

vi.mock("./rpcClient",()=>({rpc:vi.fn()}));
afterEach(()=>vi.resetAllMocks());
const input=(epoch:number)=>({request_epoch:epoch,request_revision_digest:`request-${epoch}`,operation_id:`op-${epoch}`,text:`question ${epoch}`,edit_kind:"APPEND",created_at:"2026-09-14T00:00:00Z",authored_text_ref:{revision_digest:`authored-${epoch}`}});

it("restores two distinct saved answers and never assigns a different request's result",async()=>{
  vi.mocked(rpc).mockImplementation(async(method,scope)=>({operation_id:"query",state:"SUCCEEDED",value:method==="thread/activity/list"?{conversation:{turns:[input(1),input(2)],next_before_epoch:null,history_limited:false}}:{operation_id:scope.operation_id,state:"SUCCEEDED",result:{thread_id:"t",request_epoch:scope.operation_id==="op-1"?1:2,answer:`answer ${scope.operation_id}`}}}));
  const page=await readConversation("p","t",null);
  expect(page.turns.map(t=>t.result?.answer)).toEqual(["answer op-1","answer op-2"]);
  const updated=withCurrentCheckpoint(page.turns,{operation_state:"RUNNING",current_result:{request_ref:{revision_digest:"request-2"},operation_id:"op-2",result:{answer:"checkpoint"}}});
  expect(updated.map(t=>t.result?.answer)).toEqual(["answer op-1","checkpoint"]);
});

it("does not use current text for an inaccessible or mismatched historical answer",async()=>{
  vi.mocked(rpc).mockImplementation(async(method,scope)=>({operation_id:"query",state:"SUCCEEDED",value:method==="thread/activity/list"?{conversation:{turns:[input(1)],next_before_epoch:null,history_limited:false}}:{operation_id:scope.operation_id,state:"SUCCEEDED",result:{thread_id:"other",request_epoch:1,answer:"foreign"}}}));
  const page=await readConversation("p","t",null);
  expect(page.turns[0].result).toBeNull();expect(page.turns[0].unavailable).toBeTruthy();
});

it("uses digest and operation together for checkpoint binding",()=>{
  const turns:ConversationTurn[]=[{input:input(1),state:"SUCCEEDED",result:{answer:"old"}}];
  expect(withCurrentCheckpoint(turns,{current_result:{request_ref:{revision_digest:"request-1"},operation_id:"other",result:{answer:"wrong"}}})[0].result?.answer).toBe("old");
});

it('retains failure and does not turn an initial checkpoint into an answer',()=>{
  const error={message:'research execution failed',data:{reason_code:'INTERNAL'}};
  const turns:ConversationTurn[]=[{input:input(1),state:'FAILED',result:null,error}];
  const rendered=withCurrentCheckpoint(turns,{operation_state:'FAILED',current_result:{request_ref:{revision_digest:'request-1'},operation_id:'op-1',result:{message:'Examining sources'},completion:'CHECKPOINT'}})[0];
  expect(rendered.result).toBeNull();expect(rendered.error).toBe(error);expect(rendered.state).toBe('FAILED');
});

it('never fills a denied historical result from a current checkpoint',()=>{
  const turns:ConversationTurn[]=[{input:input(1),state:'UNAVAILABLE',result:null,unavailable:'denied'}];
  expect(withCurrentCheckpoint(turns,{current_result:{request_ref:{revision_digest:'request-1'},operation_id:'op-1',result:{answer:'sensitive'}}})).toEqual(turns);
});

it('retains actual partial content with the matching failed operation',()=>{
  const turns:ConversationTurn[]=[{input:input(1),state:'FAILED',result:null,error:{message:'failed'}}];
  const rendered=withCurrentCheckpoint(turns,{operation_state:'FAILED',current_result:{request_ref:{revision_digest:'request-1'},operation_id:'op-1',result:{answer:'partial observation'}}})[0];
  expect(rendered.result?.answer).toBe('partial observation');expect(rendered.state).toBe('FAILED');
});

it('does not attribute a later failed request to a readable previous answer',()=>{
  const turns:ConversationTurn[]=[{input:input(1),state:'SUCCEEDED',result:{answer:'previous answer'}}];
  const rendered=withCurrentCheckpoint(turns,{request:{operation_id:'op-2'},operation_state:'FAILED',operation_error:{message:'new request failed'},
    previous_result:{request_ref:{revision_digest:'request-1'},operation_id:'op-1',result:{answer:'previous answer'}},
    basis_currentness:{state:'REVIEW_REQUIRED',reasons:['REQUEST_CHANGED']}})[0];
  expect(rendered.result?.answer).toBe('previous answer');
  expect(rendered.state).toBe('STALE');
  expect(rendered.error).toBeUndefined();
});
