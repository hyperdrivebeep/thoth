import { t } from "../i18n/locale";
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
  const reason = t("sourceTime.correctReason");
  const [error, setError] = useState("");
  if (!assessment || !cutoff) return null;
  return <div className="source-time-advanced">
    <Button small minimal icon={open?"chevron-up":"chevron-down"} aria-expanded={open} onClick={()=>setOpen(value=>!value)}>{t("sourceTime.advanced")}</Button>
    <Collapse isOpen={open}><div className="source-time-advanced-body">
      <p>{t("labels.classification")}{" "}{cutoffLabel(assessment.cutoff_state)}</p>
      <p>{t("labels.assessmentReason")}{" "}{assessment.reason_code} · {assessment.mode}</p>
      <HTMLSelect aria-label={t("sourceTime.state")} value={assessment.cutoff_state} options={cutoffOptions} disabled />
      <p className="muted">{t("sourceTime.notice")}</p>
      {assessment.cutoff_state !== "PROHIBITED_CONTEXT" && <>
        <Button small onClick={async ()=>{ try { await correctSourceTime({project_id:projectId, artifact_id:artifactId, source_version_id:assessment.source_version_id, byte_sha256:assessment.byte_sha256, expected_project_revision:cutoff.project_revision, expected_cutoff_at:cutoff.cutoff_at, expected_assessment_revision:assessment.revision, expected_metadata_digest:assessment.metadata_digest ?? assessment.assessment_digest, assertion:"ON_OR_BEFORE_CUTOFF", correction_reason:reason}); } catch(err){ setError(err instanceof Error?err.message:String(err)); } }}>{t("sourceTime.eligible")}</Button>
        <Button small onClick={async ()=>{ try { await correctSourceTime({project_id:projectId, artifact_id:artifactId, source_version_id:assessment.source_version_id, byte_sha256:assessment.byte_sha256, expected_project_revision:cutoff.project_revision, expected_cutoff_at:cutoff.cutoff_at, expected_assessment_revision:assessment.revision, expected_metadata_digest:assessment.metadata_digest ?? assessment.assessment_digest, assertion:"AFTER_CUTOFF", correction_reason:reason}); } catch(err){ setError(err instanceof Error?err.message:String(err)); } }}>{t("sourceTime.after")}</Button>
        <Button small minimal onClick={async ()=>{ try { await correctSourceTime({project_id:projectId, artifact_id:artifactId, source_version_id:assessment.source_version_id, byte_sha256:assessment.byte_sha256, expected_project_revision:cutoff.project_revision, expected_cutoff_at:cutoff.cutoff_at, expected_assessment_revision:assessment.revision, expected_metadata_digest:assessment.metadata_digest ?? assessment.assessment_digest, revert_unknown:true, correction_reason:reason}); } catch(err){ setError(err instanceof Error?err.message:String(err)); } }}>{t("sourceTime.unknown")}</Button>
      </>}
      {error && <Callout intent="danger">{error}</Callout>}
    </div></Collapse>
  </div>;
}
