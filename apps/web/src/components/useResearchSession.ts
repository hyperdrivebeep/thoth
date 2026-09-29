import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type RefObject } from "react";
import { rpc } from "../api/rpcClient";
import type { ResearchAdmission, ResearchStatus } from "../api/research";
import type { ModelSelection } from "./ModelSettings";
import { readDraft, writeDraft } from "../api/conversation";
import { clearLocalPending, inspectLocalDraft, readLocalPending, submissionSignature, validWorkspaceId, writeLocalPending,
  type BrowserScope, type DraftWriteResult, type PendingRead, type PendingSubmission } from "../api/localWorkspacePersistence";

/** Draft ownership only. All execution/current-result states are server projections. */
export function useResearchSession(projectId: string, threadId: string, epoch: RefObject<number>, onAdmitted: (id: string) => void,
  storageScope: BrowserScope, canExecute: boolean) {
  const client = useQueryClient();
  const [problem, updateProblem] = useState(() => readDraft(projectId, threadId, storageScope));
  const problemRef = useRef(problem);
  const [draftSaveState, setDraftSaveState] = useState<DraftWriteResult>(() =>
    storageScope.mode === "LOCAL" && !validWorkspaceId(storageScope.workspaceId) ? "UNAVAILABLE" : "SAVED");
  const [draftRestoreIssue, setDraftRestoreIssue] = useState<"CORRUPT" | "UNAVAILABLE" | null>(() => {
    if (storageScope.mode !== "LOCAL") return null;
    const kind = inspectLocalDraft(storageScope.workspaceId, projectId, threadId).kind;
    return kind === "CORRUPT" || kind === "UNAVAILABLE" ? kind : null;
  });
  const [pendingRead, setPendingRead] = useState<PendingRead>(() => storageScope.mode === "LOCAL"
    ? readLocalPending(storageScope.workspaceId, projectId, threadId) : { kind: "NONE" });
  const [pendingStorageError, setPendingStorageError] = useState<string | null>(null);
  const setProblem = (text: string) => {
    const saved = writeDraft(projectId, threadId, text, storageScope);
    if (storageScope.mode === "LOCAL") {
      setDraftSaveState(saved);
      if (saved === "SAVED") setDraftRestoreIssue(null);
    }
    problemRef.current = text;
    updateProblem(text);
  };
  useEffect(() => {
    const stored = readDraft(projectId, threadId, storageScope);
    problemRef.current = stored;
    updateProblem(stored);
    setDraftSaveState(storageScope.mode === "LOCAL" && !validWorkspaceId(storageScope.workspaceId) ? "UNAVAILABLE" : "SAVED");
    if (storageScope.mode === "LOCAL") {
      const kind = inspectLocalDraft(storageScope.workspaceId, projectId, threadId).kind;
      setDraftRestoreIssue(kind === "CORRUPT" || kind === "UNAVAILABLE" ? kind : null);
    } else setDraftRestoreIssue(null);
    setPendingRead(storageScope.mode === "LOCAL" ? readLocalPending(storageScope.workspaceId, projectId, threadId) : { kind: "NONE" });
    pendingSubmission.current = null;
    setPendingStorageError(null);
  }, [projectId, threadId, storageScope]);
  const [modelSelection, setModelSelection] = useState<ModelSelection | null>(null);
  const draftRevision = useRef(0);
  const pendingSubmission = useRef<{ signature: string; key: string; inFlight: boolean } | null>(null);
  const currentSignature = submissionSignature(projectId, threadId, problem, modelSelection);
  const pendingBlocksNewInput = pendingRead.kind === "INVALID" || pendingRead.kind === "PENDING" && pendingRead.value.signature !== currentSignature;
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
    mutationFn: async (captured: { projectId: string; threadId: string; problem: string; selection: ModelSelection | null; revision: number; epoch: number; key: string; scope: BrowserScope }) => {
      const admission = await rpc<ResearchAdmission>(captured.threadId ? "thread/input" : "thread/start", {
        project_id: captured.projectId, contract_version: 2, ...captured.selection,
        ...(captured.threadId ? { thread_id: captured.threadId, instruction: captured.problem } : { problem: captured.problem }),
      }, captured.key);
      const value = admission.value;
      const sameOperation = Boolean(value?.operation_id && value.operation_id === admission.operation_id);
      const normal = value?.status === "ACCEPTED_RUNNING" && ["RUNNING", "SUCCEEDED"].includes(admission.state) &&
        Number.isInteger(value.request_epoch) && sameOperation;
      const queued = captured.threadId && value?.status === "QUEUED_AFTER_CURRENT" && admission.state === "RUNNING" && sameOperation;
      // A same-key replay may return the terminal operation result rather than
      // the earlier admission DTO. The bus has already scoped its idempotency key.
      const completedReplay = admission.state === "SUCCEEDED" && !value?.status &&
        typeof value?.terminal_reason === "string" &&
        value.terminal_reason.length > 0 && Boolean(admission.operation_id);
      if ((!normal && !queued && !completedReplay) || value?.contract_version !== 2 ||
        (value.project_id !== undefined && value.project_id !== captured.projectId) ||
        !value.thread_id || (captured.threadId && value.thread_id !== captured.threadId) ||
        ((normal || queued) && value.project_id !== captured.projectId)) {
        throw new Error("연구 요청의 접수를 확인하지 못했습니다. 초안을 보존했습니다.");
      }
      return value;
    },
    onSuccess: async (admission, captured) => {
      // Parent invalidates this token synchronously on project selection; late admissions cannot replace another draft.
      if (captured.epoch !== epoch.current) return;
      pendingSubmission.current = null;
      if (captured.scope.mode === "LOCAL") {
        const removed = clearLocalPending(captured.scope.workspaceId, captured.projectId, captured.threadId);
        if (removed) { setPendingRead({ kind: "NONE" }); setPendingStorageError(null); }
        else setPendingStorageError("접수는 확인됐지만 브라우저의 요청 복구 표시를 지우지 못했습니다. 원 요청을 다시 확인하세요.");
      }
      clearModel(captured.revision, captured.epoch);
      const next = problemRef.current === captured.problem ? "" : problemRef.current;
      if (!next) {
        const saved = writeDraft(captured.projectId, captured.threadId, "", captured.scope);
        if (captured.scope.mode === "LOCAL") setDraftSaveState(saved);
      } else if (admission.thread_id !== captured.threadId) {
        const saved = writeDraft(captured.projectId, admission.thread_id, next, captured.scope);
        if (saved === "SAVED") writeDraft(captured.projectId, captured.threadId, "", captured.scope);
        else if (captured.scope.mode === "LOCAL") setDraftSaveState(saved);
      }
      problemRef.current = next;
      updateProblem(next);
      onAdmitted(admission.thread_id);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["threads"] }),
        client.invalidateQueries({ queryKey: ["research", captured.projectId, admission.thread_id] }),
        client.invalidateQueries({ queryKey: ["conversation", captured.projectId, admission.thread_id] }),
        client.invalidateQueries({ queryKey: ["evidence", captured.projectId] }),
      ]);
    },
    onSettled: (_data, _error, captured) => { if (pendingSubmission.current?.key === captured.key) pendingSubmission.current.inFlight = false; },
  });
  const retryPending = () => {
    if (storageScope.mode !== "LOCAL" || !canExecute || pendingSubmission.current?.inFlight) return;
    const stored = readLocalPending(storageScope.workspaceId, projectId, threadId);
    setPendingRead(stored);
    if (stored.kind !== "PENDING") return;
    const { value } = stored;
    pendingSubmission.current = { signature: value.signature, key: value.key, inFlight: true };
    setPendingStorageError(null);
    submit.mutate({ projectId, threadId, problem: value.problem, selection: value.selection,
      revision: -1, epoch: epoch.current, key: value.key, scope: storageScope });
  };
  return { research, problem, setProblem, draftSaveState, draftRestoreIssue, pendingRead, pendingBlocksNewInput, pendingStorageError,
    retryPending, modelSelection, chooseModel, clearModel, draftRevision,
    renderedRevision: draftRevision.current, submit,
    send: () => {
      if (!canExecute || !problem.trim() || pendingSubmission.current?.inFlight || pendingBlocksNewInput) return;
      if (storageScope.mode === "LOCAL") {
        const stored = readLocalPending(storageScope.workspaceId, projectId, threadId);
        setPendingRead(stored);
        if (stored.kind === "INVALID" || stored.kind === "PENDING" && stored.value.signature !== currentSignature) return;
        const record: PendingSubmission = stored.kind === "PENDING" ? stored.value : {
          version: 1, workspaceId: storageScope.workspaceId ?? "", projectId, threadId,
          problem, selection: modelSelection, signature: currentSignature, key: crypto.randomUUID(), createdAt: Date.now(),
        };
        if (stored.kind === "NONE") {
          const saved = writeLocalPending(record);
          if (saved !== "SAVED") {
            setPendingStorageError(saved === "TOO_LARGE" ? "요청 복구 정보를 저장할 수 없을 만큼 초안이 큽니다. 입력을 줄인 뒤 다시 시도하세요."
              : "요청 복구 정보를 저장하지 못했습니다. 중복 접수를 막기 위해 전송하지 않았습니다.");
            return;
          }
          setPendingRead({ kind: "PENDING", value: record });
        }
        pendingSubmission.current = { signature: record.signature, key: record.key, inFlight: true };
        setPendingStorageError(null);
        submit.mutate({ projectId, threadId, problem: record.problem, selection: record.selection,
          revision: draftRevision.current, epoch: epoch.current, key: record.key, scope: storageScope });
        return;
      }
      const signature = JSON.stringify([projectId,threadId,problem,modelSelection,epoch.current]);
      if (pendingSubmission.current?.signature !== signature) pendingSubmission.current = {signature,key:crypto.randomUUID(),inFlight:false};
      const request = pendingSubmission.current;
      request.inFlight = true;
      submit.mutate({ projectId, threadId, problem, selection: modelSelection, revision: draftRevision.current, epoch: epoch.current, key:request.key, scope: storageScope });
    },
  };
}
