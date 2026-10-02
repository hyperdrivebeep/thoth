import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {ResearchResultCard} from './ResearchResultCard';

it('renders a failed initial checkpoint as a failure rather than empty answer actions',()=>{
  const html=renderToStaticMarkup(<ResearchResultCard state="FAILED" result={{message:'Examining sources'}} failure={{primary:{origin:'MODEL_CALL',reason_code:'MODEL_CALL_TIME_BUDGET_EXHAUSTED'},secondary:{origin:'HOLD_PUBLICATION',reason_code:'INTERNAL'},remote_observation:'UNKNOWN'}} onDetail={()=>{}}/>);
  expect(html).toContain('모델 응답 시간 제한');expect(html).toContain('내부 결과 저장');expect(html).toContain('원격 모델의 종료 여부는 확인되지');
  expect(html).not.toContain('답변 상세');expect(html).not.toContain('answer-text');
});
it('keeps partial content while making failure explicit and does not blame legacy input',()=>{
  const html=renderToStaticMarkup(<ResearchResultCard state="FAILED" result={{answer:'실제 관측 내용'}} error={{message:'invalid method parameters'}} onDetail={()=>{}}/>);
  expect(html).toContain('실패 전까지 확인한 내용');expect(html).toContain('실제 관측 내용');expect(html).not.toContain('invalid method parameters');
});
it('tells the user to sign in to Claude when the Claude Code profile is not signed in, without blaming the model',()=>{
  const html=renderToStaticMarkup(<ResearchResultCard state="FAILED" result={null} failure={{primary:{origin:'MODEL_CALL',reason_code:'CLAUDE_CODE_LOGIN_REQUIRED'},remote_observation:'UNKNOWN'}} onDetail={()=>{}}/>);
  expect(html).toContain('Claude 로그인이 필요합니다');expect(html).toContain('Claude로 로그인');
  expect(html).not.toContain('내부 연구 처리에 실패');expect(html).not.toContain('원격 모델의 종료 여부');expect(html).not.toContain('CLAUDE_CODE_LOGIN_REQUIRED');
});
it('tells the user when the memory a stored answer used was corrected afterwards',()=>{
  const html=renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{answer:'저장된 답변'}} currentness={{state:'REVIEW_REQUIRED',reasons:['MEMORY_CORRECTED_AFTER_RESULT']}} onDetail={()=>{}}/>);
  expect(html).toContain('재검토 필요');expect(html).toContain('이 결과가 쓴 기억이 이후 정정되었습니다');expect(html).not.toContain('MEMORY_CORRECTED_AFTER_RESULT');
});
it('does not show answer navigation for a running initial checkpoint',()=>{
  const html=renderToStaticMarkup(<ResearchResultCard state="RUNNING" result={{message:'Examining sources'}} onDetail={()=>{}}/>);
  expect(html).toContain('아직 저장된 답변은 없습니다');expect(html).not.toContain('답변 상세');
});

it('renders follow-up contract fields without inventing missing evidence or change reasons',()=>{
  const digest='a'.repeat(64);
  const html=renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{answer:'저장된 답변'}}
    progressSummary={{schema_version:'1.0.0',request_revision_digest:digest,result_revision_digest:digest,state:'HOLD',
      currentness:{state:'CURRENT',reasons:[],execution_eligible:true},progress_items:['요구조건 2개를 확인했습니다'],recorded_checks:['원문 위치 확인'],
      remaining_gaps:['외부 검증 필요'],unknowns:[],next_user_action:{schema_version:'1.0.0',action_type:'REVIEW_GAPS',label:'남은 gap 검토',reason_codes:['PARTIAL_HOLD'],requires_permission:false,target:null,basis:{}}}}
    coverageMatrix={{schema_version:'1.0.0',request_revision_digest:digest,availability:'AVAILABLE',reason_codes:[],requirement_set_revision_digest:null,coverage_revision_digest:null,
      summary:{satisfied:1,unresolved:1,not_applicable:0,not_assessed:0,hold_targets:[],reason_codes:[]},rows:[{requirement_id:'r1',target:'자료 기준',question:'자료가 현재인가',status:'UNRESOLVED',applicability:'applies',relation:'needs_review',validation:'권한 변경 뒤 재확인',blocker:'',review_refs:[],evidence_refs:[],reason_codes:['ACCESS_CHANGED']}]}}
    onDetail={()=>{}}/>);
  expect(html).toContain('답변 검토');expect(html).toContain('부분 보류');expect(html).toContain('보류된 기준의 부족한 자료 확인');expect(html).toContain('남은 gap 검토'); // wording is chosen by action type; the server label stays under 기술 정보
  expect(html).toContain('이 검토 요약에 따로 연결된 근거는 없습니다');
  expect(html).toContain('비교 대상 답변이 확정되지 않아 바뀐 판단을 표시하지 않습니다');
  expect(html).not.toContain('실행 실패');
});

it('shows a cut-off model call as a failure with a retry that only fills the question in',()=>{
  const html=renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{terminal_reason:'OAUTH_TRANSPORT_FAILURE'}} terminalReason="OAUTH_TRANSPORT_FAILURE"
    failure={{primary:{origin:'MODEL_CALL',reason_code:'OAUTH_TRANSPORT_FAILURE'},remote_observation:'UNKNOWN'}} onRetry={()=>{}} onDetail={()=>{}}/>);
  const visible=html.replace(/<details[\s\S]*?<\/details>/g,'');
  expect(visible).toContain('모델 호출이 중간에 끊겨 이 조사의 답이 없습니다(연결 오류)');
  expect(visible).toContain('같은 질문으로 다시 조사');expect(visible).toContain('질문을 입력창에 채워 둡니다');
  expect(visible).toContain('자동 재실행하지 않습니다');
  expect(visible).not.toContain('OAUTH_TRANSPORT_FAILURE');expect(visible).not.toContain('답변 상세');
});
it('recognises the same cut-off on an older turn that has only the terminal reason',()=>{
  const html=renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={null} terminalReason="OAUTH_READ_IDLE_TIMEOUT_REMOTE_STOP_UNKNOWN" onRetry={()=>{}} onDetail={()=>{}}/>);
  expect(html).toContain('모델 호출이 중간에 끊겨 이 조사의 답이 없습니다(응답 시간 초과)');expect(html).toContain('같은 질문으로 다시 조사');
});
it('does not show a retry or the cut-off wording for an ordinary evidence hold',()=>{
  const html=renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{answer:'검증 보류 · 부분 초안: 근거가 부족합니다',answer_status:'PARTIAL_HOLD'}} terminalReason="BOUNDED_RESEARCH_COMPLETE" onRetry={()=>{}} onDetail={()=>{}}/>);
  expect(html).not.toContain('모델 호출이 중간에 끊겨');expect(html).not.toContain('같은 질문으로 다시 조사');
});
it('labels facts confirmed before the interruption',()=>{
  const spanId='span:aaaa1111-0000-0000-0000-000000000000';
  const html=renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{confirmed_findings:[{statement:'문서에 적힌 사실',evidence_refs:[spanId],requirement_id:null,kind:'DOCUMENT_FACT'}]}} terminalReason="OAUTH_TRANSPORT_FAILURE" onDetail={()=>{}}/>);
  expect(html).toContain('중단 전까지 확인한 사실');expect(html).toContain('문서에 적힌 사실');
});
it('names a stalled, over-long or never-ending call as a cut-off and offers to resume from where it stopped',()=>{
  for(const [code,label] of [['OAUTH_STALLED_STREAM_REMOTE_STOP_UNKNOWN','응답이 매우 느려짐'],['OAUTH_DISPATCH_DEADLINE_REMOTE_STOP_UNKNOWN','호출 시간 초과'],['OAUTH_RUNAWAY_OUTPUT_REMOTE_STOP_UNKNOWN','응답이 끝나지 않고 계속 생성됨']]){
    const html=renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={null} terminalReason={code}
      failure={{primary:{origin:'MODEL_CALL',reason_code:code},remote_observation:'UNKNOWN'}} onRetry={()=>{}} onResume={()=>{}} resumeCompleted={3} onDetail={()=>{}}/>);
    const visible=html.replace(/<details[\s\S]*?<\/details>/g,'');
    expect(visible).toContain('모델 호출이 중간에 끊겨 이 조사의 답이 없습니다('+label+')');
    expect(visible).toContain('이어서 조사');expect(visible).toContain('끝낸 3단계는 그대로 쓰고');
    expect(visible).toContain('같은 질문으로 다시 조사');expect(visible).not.toContain(code);
  }
});
it('offers to resume only a cut-off result that has finished stages and a caller that can resume',()=>{
  const cut={state:'SUCCEEDED',result:null,terminalReason:'OAUTH_TRANSPORT_FAILURE',onRetry:()=>{},onDetail:()=>{}};
  expect(renderToStaticMarkup(<ResearchResultCard {...cut} onResume={()=>{}} resumeCompleted={0}/>)).not.toContain('이어서 조사');
  expect(renderToStaticMarkup(<ResearchResultCard {...cut} resumeCompleted={2}/>)).not.toContain('이어서 조사');
  const hold=renderToStaticMarkup(<ResearchResultCard state="SUCCEEDED" result={{answer:'검증 보류 · 부분 초안',answer_status:'PARTIAL_HOLD'}} terminalReason="BOUNDED_RESEARCH_COMPLETE" onResume={()=>{}} resumeCompleted={2} onDetail={()=>{}}/>);
  expect(hold).not.toContain('이어서 조사');
});

