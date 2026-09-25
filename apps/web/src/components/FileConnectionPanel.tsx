import { Button, Callout, FileInput, HTMLSelect } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { stageFile } from "../api/files";
import { rpc } from "../api/rpcClient";
import { authorityOptions, sourceConnectSchema } from "../api/sourcePolicy";

export function FileConnectionPanel({ projectId, hosted = false }: { projectId: string; hosted?: boolean }) {
  const client = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const [authority, setAuthority] = useState("UNCLASSIFIED");
  const [visibility, setVisibility] = useState("");
  const [progress, setProgress] = useState("");
  const connect = useMutation({ mutationFn: async (captured: {files: File[]; authority: string; visibility: string}) => {
    for (const file of captured.files) {
      setProgress(`${file.name} · 업로드 준비 / 구조 파싱`);
      const staged = await stageFile(file, projectId);
      await rpc("project/source/connect", sourceConnectSchema.parse({project_id:projectId, relative_path:staged.relative_path, media_type:staged.media_type,
        authority:captured.authority, cutoff_state:"ELIGIBLE", security_class:"INTERNAL",
        resource_scope:{owner_kind:"PROJECT",visibility:captured.visibility}, version_label:`web-${staged.byte_sha256.slice(0,12)}`}), crypto.randomUUID());
    }
  }, onSuccess: async () => {
    setFiles([]); setProgress("");
    if(fileInput.current)fileInput.current.value="";
    await Promise.all([client.invalidateQueries({queryKey:["sources",projectId]}),client.invalidateQueries({queryKey:["evidence",projectId]}),client.invalidateQueries({queryKey:["research",projectId]})]);
  }, onError: () => setProgress("") });
  return <section className="source-connect-panel">
    <div className="section-title-row"><div><p className="eyebrow">AUTHORIZED SOURCES</p><h2>파일 자료 연결</h2></div><span className="muted">원문 · 버전 · 위치 보존</span></div>
    <p className="muted">HWPX · PDF · DOCX · XLSX · CSV · JSON · MD · TXT · HTML 파일을 올리고, 자료의 성격과 사용 범위를 함께 기록합니다.{hosted?" 업로드한 파일은 이 심사 프로젝트에서 사용됩니다.":" 파일은 이 컴퓨터의 THOTH 작업공간에 준비됩니다."}</p>
    <div className="source-controls"><FileInput fill text={files.length ? `${files.length}개 선택` : "파일 선택…"} inputProps={{ref:fileInput,multiple:true,"aria-label":"연결할 파일 자료"}} onInputChange={event => setFiles(Array.from((event.target as HTMLInputElement).files ?? []))} />
      <label className="source-control-label"><span>자료 성격</span><HTMLSelect aria-label="자료 성격" value={authority} onChange={event=>setAuthority(event.target.value)} options={authorityOptions} /></label>
      <label className="source-control-label"><span>사용 범위</span><HTMLSelect aria-label="자료 사용 범위" value={visibility} onChange={event=>setVisibility(event.target.value)} options={[{value:"",label:"사용 범위를 선택하세요"},{value:"PROJECT_SHARED",label:"이 프로젝트에서 함께 사용"},{value:"EXPLICIT_GRANT",label:"명시적으로 허용된 작업만"}]} /></label>
      <Button icon="upload" intent="primary" disabled={!files.length || !visibility} loading={connect.isPending} onClick={()=>connect.mutate({files,authority,visibility})}>자료 연결</Button></div>
    {files.length > 0 && <p className="muted">{files.map(file=>file.name).join(" · ")}</p>}{progress && <p role="status">{progress}</p>}
    {connect.error && <Callout compact intent="danger" title="자료 연결 실패 / 부분 연결 가능">{connect.error.message} 이미 연결된 파일은 자료 목록에서 확인하세요.</Callout>}
    {connect.isSuccess && <Callout compact intent="success">자료 연결이 완료되었습니다. 분석은 새 질문이나 지시로 시작합니다.</Callout>}
  </section>;
}
