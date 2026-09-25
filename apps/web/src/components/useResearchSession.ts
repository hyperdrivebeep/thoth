import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type RefObject } from "react";
import { rpc } from "../api/rpcClient";
import type { ResearchAdmission, ResearchStatus } from "../api/research";
import type { ModelSelection } from "./ModelSettings";
import { readDraft, writeDraft } from "../api/conversation";

/** Draft ownership only. All execution/current-result states are server projections. */
export function useResearchSession(projectId: string, threadId: string, epoch: RefObject<number>, onAdmitted: (id: string) => void) {
  const client = useQueryClient();
  const [problem, updateProblem] = useState(() => readDraft(projectId, threadId));
  const setProblem = (text: string) => { writeDraft(projectId, threadId, text); updateProblem(text); };
  useEffect(() => { updateProblem(readDraft(projectId, threadId)); }, [projectId, threadId]);
  const [modelSelection, setModelSelection] = useState<ModelSelection | null>(null);
  const draftRevision = useRef(0);
  const pendingSubmission = useRef<{ signature: string; key: string; inFlight: boolean } | null>(null);
  const chooseModel = (selection: ModelSelection) => { draftRevision.current += 1; setModelSelection(selection); };
  const clearModel = (revision: number, capturedEpoch = epoch.current) => {
    if (draftRevision.current === revision && capturedEpoch === epoch.current) { draftRevision.current += 1; setModelSelection(null); }
  };
  const readKey = ["research", projectId, threadId];
  const research = useQuery({
    queryKey: readKey, enabled: Boolean(projectId && threadId),
    queryFn: ({ signal }) => rpc<ResearchStatus>("thread/read", { project_id: projectId, thread_id: threadId }, crypto.randomUUID(), signal),
    refetchInterval: query => query.state.data?.value.operation_state === "RUNNING" ? 1200 : false,
  });
  const basis = research.data?.value.current_result?.basis_digest;
  const operationState = research.data?.value.operation_state;
  useEffect(() => {
    if (!threadId || (!basis && !operationState)) return;
    void client.invalidateQueries({queryKey:["evidence",projectId]});
    void client.invalidateQueries({queryKey:["sources",projectId]});
    void client.invalidateQueries({queryKey:["conversation",projectId,threadId]});
  }, [basis,operationState,projectId,threadId,client]);
  const submit = useMutation({
    mutationFn: async (captured: { projectId: string; threadId: string; problem: string; selection: ModelSelection | null; revision: number; epoch: number; key: string }) => {
      const admission = await rpc<ResearchAdmission>(captured.threadId ? "thread/input" : "thread/start", {
        project_id: captured.projectId, contract_version: 2, ...captured.selection,
        ...(captured.threadId ? { thread_id: captured.threadId, instruction: captured.problem } : { problem: captured.problem }),
      }, captured.key);
      return admission.value;
    },
    onSuccess: async (admission, captured) => {
      // Parent invalidates this token synchronously on project selection; late admissions cannot replace another draft.
      if (captured.epoch !== epoch.current) return;
      pendingSubmission.current = null;
      clearModel(captured.revision, captured.epoch);
      updateProblem(current => {
        const next = current === captured.problem ? "" : current;
        writeDraft(captured.projectId, captured.threadId, "");
        writeDraft(captured.projectId, admission.thread_id, next);
        return next;
      });
      onAdmitted(admission.thread_id);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["threads", captured.projectId] }),
        client.invalidateQueries({ queryKey: ["research", captured.projectId, admission.thread_id] }),
        client.invalidateQueries({ queryKey: ["conversation", captured.projectId, admission.thread_id] }),
        client.invalidateQueries({ queryKey: ["evidence", captured.projectId] }),
      ]);
    },
    onSettled: (_data, _error, captured) => { if (pendingSubmission.current?.key === captured.key) pendingSubmission.current.inFlight = false; },
  });
  return { research, problem, setProblem, modelSelection, chooseModel, clearModel, draftRevision,
    renderedRevision: draftRevision.current, submit,
    send: () => {
      if (!problem.trim() || pendingSubmission.current?.inFlight) return;
      const signature = JSON.stringify([projectId,threadId,problem,modelSelection,epoch.current]);
      if (pendingSubmission.current?.signature !== signature) pendingSubmission.current = {signature,key:crypto.randomUUID(),inFlight:false};
      const request = pendingSubmission.current;
      request.inFlight = true;
      submit.mutate({ projectId, threadId, problem, selection: modelSelection, revision: draftRevision.current, epoch: epoch.current, key:request.key });
    },
  };
}
