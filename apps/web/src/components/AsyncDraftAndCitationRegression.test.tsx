// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import { useCitationGate } from './SourceCitationGate';
import { FirstRunSetup } from './FirstRunSetup';
import { setLocale, t, useTranslation } from '../i18n/locale';

const fake=vi.hoisted(()=>({resolveRegister:null as null|((v:unknown)=>void),sent:[] as unknown[],
 unknown:false,resolveConfirm:null as null|((v:unknown)=>void)}));
vi.mock('../api/rpcClient',async(importOriginal)=>{
 const original=await importOriginal<Record<string,unknown>>();
 return {...original,rpc:async(method:string,input:Record<string,unknown>)=>{
  if(method==='project/source/list')return {value:{artifacts:['a','b'].map(id=>({artifact_id:id,source_uri:id+'.txt',byte_sha256:'a'.repeat(64),cutoff_state:fake.unknown?'UNKNOWN_TIME':'ELIGIBLE'})),
   source_times:fake.unknown?['a','b'].map(id=>({artifact_id:id,source_version_id:'v:'+id,byte_sha256:'a'.repeat(64),cutoff_state:'UNKNOWN_TIME',revision:1,assessment_digest:'a'.repeat(64),cutoff_at:'2026-10-01T00:00:00Z'})):[],cutoff_basis:{cutoff_at:'2026-10-01T00:00:00Z',project_revision:1}}};
  if(method==='project/source/time/confirm')return new Promise(resolve=>{fake.resolveConfirm=resolve;});
  if(method==='workspace/ready')return {value:{ready:false,model_connected:false,setup_complete:false,workspace_readable:true,execution_ready:false,workspace_id:'workspace:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',setup:{schema_version:1,revision:1,internet_consent:'UNDECIDED',internet_grant_id:null}}};
  if(method==='model/credential/list')return {value:{accounts:[{provider:'openai',label:'ChatGPT',connected:false,has_key:false,oauth:false,login_supported:false,login_kind:'unsupported'}]}};
  if(method==='model/credential/register'){fake.sent.push(input);return new Promise(resolve=>{fake.resolveRegister=resolve;});}
  throw new Error('Unexpected synthetic RPC '+method);
 }};
});
let root:Root,el:HTMLDivElement,client:QueryClient;
async function mount(ui:React.ReactNode){
 Object.assign(globalThis,{IS_REACT_ACT_ENVIRONMENT:true});setLocale('ko');
 el=document.createElement('div');document.body.append(el);root=createRoot(el);
 client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});
 await act(async()=>{root.render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)});
 await tick();
}
async function tick(){await act(async()=>{await new Promise(resolve=>setTimeout(resolve,30))})}
afterEach(async()=>{if(root)await act(async()=>root.unmount());client?.clear();el?.remove();setLocale('ko');fake.resolveRegister=null;fake.sent=[];fake.unknown=false;fake.resolveConfirm=null});
function Surface(){useTranslation();const gate=useCitationGate('project:synthetic');return <><button onClick={()=>void gate.ask('a')}>ask</button><output>{gate.message}</output></>}
it('expected locale change updates existing citation notice',async()=>{
 await mount(<Surface/>);await act(async()=>el.querySelector('button')!.click());await tick();
 const before=el.querySelector('output')!.textContent;
 expect(before).toBe(t('citation.ready'));
 await act(async()=>setLocale('en'));
 const after=el.querySelector('output')!.textContent,expected=t('citation.ready');
 expect(after).toBe(expected);
});
it('expected newer API key draft survives older submission completion',async()=>{
 await mount(<FirstRunSetup onDone={()=>{}}/>);
 const article=[...el.querySelectorAll('article')].find(a=>a.querySelector('strong')?.textContent==='ChatGPT')!;
 expect(article).toBeTruthy();
 await act(async()=>article.querySelector<HTMLButtonElement>('.provider-key-toggle')!.click());
 const input=el.querySelector<HTMLInputElement>('input[type=password]')!;
 const change=async(v:string)=>{await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,v);input.dispatchEvent(new Event('input',{bubbles:true}))})};
 await change('SYNTHETIC_OLD_NOT_A_REAL_KEY');
 const submit=[...article.querySelectorAll('button')].find(b=>b.textContent===t('setup.registerKey'))!;
 await act(async()=>submit.click());await tick();expect(fake.resolveRegister).not.toBeNull();
 await change('SYNTHETIC_NEW_NOT_A_REAL_KEY');
 const before=input.value;
 await act(async()=>fake.resolveRegister!({value:{credential:{provider:'openai',connected:true}}}));await tick();
 const remaining=el.querySelector<HTMLInputElement>('input[type=password]')?.value??null;
 expect(remaining).toBe(before);
});

function RaceSurface(){const gate=useCitationGate('project:synthetic');return <><button onClick={()=>void gate.ask('a')}>ask-a</button><button onClick={()=>void gate.ask('b')}>ask-b</button>{gate.dialog}</>}
it('expected newer citation dialog survives older confirmation completion',async()=>{
 fake.unknown=true;await mount(<RaceSurface/>);
 const click=async(text:string)=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent===text);expect(b).toBeTruthy();await act(async()=>b!.click());await tick()};
 await click('ask-a');await click(t('sourceTime.existed'));expect(fake.resolveConfirm).not.toBeNull();
 await click(t('sourceTime.skip'));await click('ask-b');
 const before=document.querySelector('.bp6-dialog')?.textContent??null;expect(before).toContain('b.txt');
 await act(async()=>fake.resolveConfirm!({value:{source_time:{}}}));await tick();
 const after=document.querySelector('.bp6-dialog')?.textContent??null;
 expect(after??'').toContain('b.txt');
});
