import { t, useTranslation } from "../i18n/locale";
import type { MessageKey } from "../i18n/messages";
import { useLayoutEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { rpc } from "../api/rpcClient";
import { confirmSourceTime, refreshSourceTimeViews } from "../api/sourceTime";
import { SourceTimeConfirmationDialog } from "./SourceTimeConfirmationDialog";
import { useDraftGeneration, type DraftTicket } from "./useDraftGeneration";

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

export function useCitationGate(projectId: string, scopeCurrent?: () => boolean) {
  useTranslation();
  const client = useQueryClient();
  const ownership = useDraftGeneration(projectId);
  const active = useRef(true);
  const currentProject = useRef(projectId);
  currentProject.current = projectId;
  useLayoutEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const isCurrent = () => active.current && currentProject.current === projectId && (scopeCurrent?.() ?? true);
  const [pending, setPending] = useState<null | {artifactId:string; fileLabel:string; cutoffAt:string; assessment: NonNullable<SourceList["source_times"]>[number]; cutoff: NonNullable<SourceList["cutoff_basis"]>; ticket: DraftTicket}>(null);
  const [messageKey, setMessageKey] = useState<MessageKey | null>(null);
  const ask = async (artifactId: string) => {
    if (!isCurrent()) return null;
    ownership.advance();
    const ticket = ownership.capture();
    const current = () => isCurrent() && ownership.isCurrent(ticket);
    setPending(null); setMessageKey(null);
    let result: Awaited<ReturnType<typeof requestCitationUse>>;
    try { result = await requestCitationUse(projectId, artifactId); }
    catch (error) { if (!current()) return null; throw error; }
    if (!current()) return null;
    await client.cancelQueries({queryKey:["sources",projectId]});
    if (!current()) return null;
    client.setQueryData(["sources",projectId], result.listed);
    if (result.status === "ELIGIBLE") { setMessageKey("citation.ready"); return result; }
    if (result.status === "AFTER_CUTOFF") { setMessageKey("citation.afterCutoff"); return result; }
    if (result.status === "PROHIBITED_CONTEXT") { setMessageKey("citation.prohibited"); return result; }
    if (result.assessment && result.cutoff && result.artifact) {
      setPending({artifactId, fileLabel: result.artifact.source_uri, cutoffAt: result.cutoff.cutoff_at, assessment: result.assessment, cutoff: result.cutoff, ticket});
    }
    return result;
  };
  const confirm = async (captured: NonNullable<typeof pending>, assertion: "ON_OR_BEFORE_CUTOFF" | "AFTER_CUTOFF") => {
    if (!isCurrent() || !ownership.isCurrent(captured.ticket)) return;
    await confirmSourceTime({project_id:projectId, artifact_id:captured.artifactId, source_version_id:captured.assessment.source_version_id, byte_sha256:captured.assessment.byte_sha256, expected_project_revision:captured.cutoff.project_revision, expected_cutoff_at:captured.cutoff.cutoff_at, expected_assessment_revision:captured.assessment.revision, expected_metadata_digest:captured.assessment.metadata_digest ?? captured.assessment.assessment_digest, assertion});
    await refreshSourceTimeViews(client,projectId);
    if (!isCurrent() || !ownership.isCurrent(captured.ticket)) return;
    ownership.advance(); setPending(null);
    setMessageKey(assertion === "ON_OR_BEFORE_CUTOFF" ? "citation.confirmed" : "citation.afterCutoff");
  };
  const dialog = pending ? <SourceTimeConfirmationDialog open fileLabel={pending.fileLabel} cutoffAt={pending.cutoffAt}
    onConfirm={() => confirm(pending, "ON_OR_BEFORE_CUTOFF")}
    onAfter={() => confirm(pending, "AFTER_CUTOFF")}
    onSkip={() => { ownership.advance(); setPending(null); }} /> : null;
  return {ask, dialog, message: messageKey ? t(messageKey) : ""};
}

