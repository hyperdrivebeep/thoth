import { Button, Callout, Collapse, HTMLSelect } from "@blueprintjs/core";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { cutoffOptions } from "../api/sourcePolicy";
import { correctSourceTime as persistSourceTime, cutoffLabel, refreshSourceTimeViews } from "../api/sourceTime";

export function SourceTimeAdvanced({projectId, artifactId, assessment, cutoff}:{projectId:string; artifactId:string; assessment?: {source_version_id:string; byte_sha256:string; cutoff_state:string; revision:number; assessment_digest:string; metadata_digest?:string; reason_code:string; cutoff_at:string; mode:string}; cutoff?: {cutoff_at:string; project_revision:number};}) {
  const client = useQueryClient();
  const correctSourceTime = async (input: Parameters<typeof persistSourceTime>[0]) => {
    await persistSourceTime(input);
    await refreshSourceTimeViews(client, input.project_id);
  };
  const [open, setOpen] = useState(false);
  const reason = "날짜 근거 정정";
  const [error, setError] = useState("");
  if (!assessment || !cutoff) return null;
  return <div className="source-time-advanced">
    <Button small minimal icon={open?"chevron-up":"chevron-down"} aria-expanded={open} onClick={()=>setOpen(value=>!value)}>고급 · 자료의 기준시점</Button>
    <Collapse isOpen={open}><div className="source-time-advanced-body">
      <p>현재 분류: {cutoffLabel(assessment.cutoff_state)}</p>
      <p>판정 이유: {assessment.reason_code} · {assessment.mode}</p>
      <HTMLSelect aria-label="기준시점 상태" value={assessment.cutoff_state} options={cutoffOptions} disabled />
      <p className="muted">상태 드롭다운만으로 이후 자료를 합격 처리할 수 없습니다. 사용자가 확인한 경우에만 바꿉니다.</p>
      {assessment.cutoff_state !== "PROHIBITED_CONTEXT" && <>
        <Button small onClick={async ()=>{ try { await correctSourceTime({project_id:projectId, artifact_id:artifactId, source_version_id:assessment.source_version_id, byte_sha256:assessment.byte_sha256, expected_project_revision:cutoff.project_revision, expected_cutoff_at:cutoff.cutoff_at, expected_assessment_revision:assessment.revision, expected_metadata_digest:assessment.metadata_digest ?? assessment.assessment_digest, assertion:"ON_OR_BEFORE_CUTOFF", correction_reason:reason}); } catch(err){ setError(err instanceof Error?err.message:String(err)); } }}>기준시점까지 존재로 정정</Button>
        <Button small onClick={async ()=>{ try { await correctSourceTime({project_id:projectId, artifact_id:artifactId, source_version_id:assessment.source_version_id, byte_sha256:assessment.byte_sha256, expected_project_revision:cutoff.project_revision, expected_cutoff_at:cutoff.cutoff_at, expected_assessment_revision:assessment.revision, expected_metadata_digest:assessment.metadata_digest ?? assessment.assessment_digest, assertion:"AFTER_CUTOFF", correction_reason:reason}); } catch(err){ setError(err instanceof Error?err.message:String(err)); } }}>기준시점보다 후로 정정</Button>
        <Button small minimal onClick={async ()=>{ try { await correctSourceTime({project_id:projectId, artifact_id:artifactId, source_version_id:assessment.source_version_id, byte_sha256:assessment.byte_sha256, expected_project_revision:cutoff.project_revision, expected_cutoff_at:cutoff.cutoff_at, expected_assessment_revision:assessment.revision, expected_metadata_digest:assessment.metadata_digest ?? assessment.assessment_digest, revert_unknown:true, correction_reason:reason}); } catch(err){ setError(err instanceof Error?err.message:String(err)); } }}>미확인으로 되돌림</Button>
      </>}
      {error && <Callout intent="danger">{error}</Callout>}
    </div></Collapse>
  </div>;
}
