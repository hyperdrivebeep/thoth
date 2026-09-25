import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";
import { confirmSourceTime, refreshSourceTimeViews } from "../api/sourceTime";
import { SourceTimeConfirmationDialog } from "./SourceTimeConfirmationDialog";

type SourceList = {
  artifacts: Array<{artifact_id:string; source_uri:string; byte_sha256:string; cutoff_state:string}>;
  source_times?: Array<{artifact_id:string; source_version_id:string; byte_sha256:string; cutoff_state:string; revision:number; assessment_digest:string; metadata_digest?:string; cutoff_at:string}>;
  cutoff_basis?: {cutoff_at:string; project_revision:number};
};

export async function requestCitationUse(projectId: string, artifactId: string) {
  const listed = await rpc<SourceList>("project/source/list", {project_id: projectId}, crypto.randomUUID());
  const artifact = listed.value.artifacts.find(item => item.artifact_id === artifactId);
  const assessment = (listed.value.source_times ?? []).find(item => item.artifact_id === artifactId);
  const cutoff = listed.value.cutoff_basis;
  if (!artifact || !assessment || !cutoff) return {status: artifact?.cutoff_state ?? "UNKNOWN_TIME", assessment, cutoff, artifact, listed};
  return {status: assessment.cutoff_state, assessment, cutoff, artifact, listed};
}

export function useCitationGate(projectId: string) {
  const client = useQueryClient();
  const [pending, setPending] = useState<null | {artifactId:string; fileLabel:string; cutoffAt:string; assessment: NonNullable<SourceList["source_times"]>[number]; cutoff: NonNullable<SourceList["cutoff_basis"]>}>(null);
  const [message, setMessage] = useState("");
  const ask = async (artifactId: string) => {
    const result = await requestCitationUse(projectId, artifactId);
    await client.cancelQueries({queryKey:["sources",projectId]});
    client.setQueryData(["sources",projectId], result.listed);
    if (result.status === "ELIGIBLE") { setMessage("인용 준비됨"); return result; }
    if (result.status === "AFTER_CUTOFF") { setMessage("기준시점보다 이후라 인용에 넣지 않습니다."); return result; }
    if (result.status === "PROHIBITED_CONTEXT") { setMessage("사용 금지 자료입니다. 시점 확인으로 해제되지 않습니다."); return result; }
    if (result.assessment && result.cutoff && result.artifact) {
      setPending({artifactId, fileLabel: result.artifact.source_uri, cutoffAt: result.cutoff.cutoff_at, assessment: result.assessment, cutoff: result.cutoff});
    }
    return result;
  };
  const dialog = pending ? <SourceTimeConfirmationDialog open fileLabel={pending.fileLabel} cutoffAt={pending.cutoffAt}
    onConfirm={async () => { await confirmSourceTime({project_id:projectId, artifact_id:pending.artifactId, source_version_id:pending.assessment.source_version_id, byte_sha256:pending.assessment.byte_sha256, expected_project_revision:pending.cutoff.project_revision, expected_cutoff_at:pending.cutoff.cutoff_at, expected_assessment_revision:pending.assessment.revision, expected_metadata_digest:pending.assessment.metadata_digest ?? pending.assessment.assessment_digest, assertion:"ON_OR_BEFORE_CUTOFF"}); await refreshSourceTimeViews(client,projectId); setPending(null); setMessage("시점을 확인했습니다. 인용 준비됨"); }}
    onAfter={async () => { await confirmSourceTime({project_id:projectId, artifact_id:pending.artifactId, source_version_id:pending.assessment.source_version_id, byte_sha256:pending.assessment.byte_sha256, expected_project_revision:pending.cutoff.project_revision, expected_cutoff_at:pending.cutoff.cutoff_at, expected_assessment_revision:pending.assessment.revision, expected_metadata_digest:pending.assessment.metadata_digest ?? pending.assessment.assessment_digest, assertion:"AFTER_CUTOFF"}); await refreshSourceTimeViews(client,projectId); setPending(null); setMessage("기준시점보다 이후라 인용에 넣지 않습니다."); }}
    onSkip={() => { setPending(null); }} /> : null;
  return {ask, dialog, message, setMessage};
}

