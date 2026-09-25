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
  expect(html).toContain('답변 검토');expect(html).toContain('부분 보류');expect(html).toContain('남은 gap 검토');
  expect(html).toContain('직접 근거 ref가 아직 기록되지 않았습니다');
  expect(html).toContain('비교 대상 답변이 확정되지 않아 바뀐 판단을 표시하지 않습니다');
  expect(html).not.toContain('실행 실패');
});
