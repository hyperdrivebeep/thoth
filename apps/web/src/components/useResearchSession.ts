import { UiError } from "../i18n/UiError";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { rpc } from "../api/rpcClient";
import { readResearchStatus } from "../api/researchStatusRead";
import { useReadAccessProtection } from "./useReadAccessProtection";
import { clearSubmission, hasSubmissionStore, readSubmission, submissionScopeId, writeSubmission } from "../api/submissionPersistence";
import { useResponseLanguageDefault } from "../api/responseLanguageDefault";
import { useResponseLanguageSupport } from "./useResponseLanguageSupport";
import type { ResponseLanguage } from "../api/localWorkspacePersistence";
import type { ResearchAdmission } from "../api/research";
import type { ModelSelection } from "./ModelSettings";
import { readDraft, writeDraft } from "../api/conversation";
import { inspectLocalDraft, submissionSignature, validWorkspaceId,
  type BrowserScope, type DraftWriteResult, type PendingOrigin, type PendingRead, type PendingSubmission } from "../api/localWorkspacePersistence";

/** Draft ownership only. All execution/current-result states are server projections. */
export function useResearchSession(projectId: string, threadId: string, epoch: RefObject<number>, onAdmitted: (id: string) => void,
  storageScope: BrowserScope, canExecute: boolean,
  /** The trace row this question is about (set by "원인 조사"); it goes with the next request only, and is dropped when the question is emptied. */
  origin: PendingOrigin | null = null, onOriginDone?: () => void) {
  const client = useQueryClient();
  const scopeEpoch = epoch.current;
  const scopeIdentity = JSON.stringify([storageScope.mode, submissionScopeId(storageScope), projectId, threadId]);
  const currentScope = useRef(scopeIdentity);
  currentScope.current = scopeIdentity;
  const active = useRef(true);
  useLayoutEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const isCurrentScope = () => active.current && currentScope.current === scopeIdentity && epoch.current === scopeEpoch;
  // The default set in Settings: null is automatic (the request names no language), otherwise ko or en on every new request.
  const responseLanguage = useResponseLanguageDefault(storageScope);
  const languageSupported = useResponseLanguageSupport(storageScope);
  const [problem, updateProblem] = useState(() => readDraft(projectId, threadId, storageScope));
  const problemRef = useRef(problem);
  const [draftSaveState, setDraftSaveState] = useState<DraftWriteResult>(() =>
    storageScope.mode === "LOCAL" && !validWorkspaceId(storageScope.workspaceId) ? "UNAVAILABLE" : "SAVED");
  const [draftRestoreIssue, setDraftRestoreIssue] = useState<"CORRUPT" | "UNAVAILABLE" | null>(() => {
    if (storageScope.mode !== "LOCAL") return null;
    const kind = inspectLocalDraft(storageScope.workspaceId, projectId, threadId).kind;
    return kind === "CORRUPT" || kind === "UNAVAILABLE" ? kind : null;
  });
  const [pendingRead, setPendingRead] = useState<PendingRead>(() => readSubmission(storageScope, projectId, threadId));
  const [pendingStorageError, setPendingStorageError] = useState<"submission.clearFailed" | "submission.tooLarge" | "submission.storageFailed" | null>(null);
  const [queuedNotice, setQueuedNotice] = useState(false);
  const setProblem = (text: string) => {
    if (origin && !text.trim()) onOriginDone?.();
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
    setPendingRead(readSubmission(storageScope, projectId, threadId));
    pendingSubmission.current = null;
    setPendingStorageError(null);
    setQueuedNotice(false);
  }, [projectId, threadId, storageScope]);
  const [modelSelection, setModelSelection] = useState<ModelSelection | null>(null);
  const draftRevision = useRef(0);
  const pendingSubmission = useRef<{ signature: string; key: string; inFlight: boolean } | null>(null);
  const chooseModel = (selection: ModelSelection) => { draftRevision.current += 1; setModelSelection(selection); };
  const clearModel = (revision: number, capturedEpoch = epoch.current) => {
    if (draftRevision.current === revision && capturedEpoch === epoch.current) { draftRevision.current += 1; setModelSelection(null); }
  };
  const readKey = ["research", projectId, threadId];
  const researchQuery = useQuery({
    queryKey: readKey, enabled: Boolean(projectId && threadId),
    queryFn: ({ signal }) => readResearchStatus(client, readKey, projectId, threadId, signal),
    refetchInterval: query => query.state.data?.value.operation_state === "RUNNING" ? 1200 : false,
  });
  const researchReadDenied = useReadAccessProtection(readKey, researchQuery.error);
  const research = researchReadDenied ? { ...researchQuery, data: undefined } : researchQuery;
  const currentSignature = submissionSignature(projectId, threadId, problem, modelSelection, origin, responseLanguage ?? undefined);
  const pendingBlocksNewInput = pendingRead.kind === "INVALID" || pendingRead.kind === "PENDING" && pendingRead.value.signature !== currentSignature;
  const basis = research.data?.value.current_result?.basis_digest;
  const operationState = research.data?.value.operation_state;
  useEffect(() => {
    if (!threadId || (!basis && !operationState)) return;
    void client.invalidateQueries({queryKey:["evidence",projectId]});
    void client.invalidateQueries({queryKey:["sources",projectId]});
    void client.invalidateQueries({queryKey:["conversation",projectId,threadId]});
  }, [basis,operationState,projectId,threadId,client]);
  // A queued instruction waits for the running investigation; the notice ends when that run leaves RUNNING.
  const sawRunning = useRef(false);
  useEffect(() => {
    if (researchReadDenied) return;
    if (!queuedNotice) { sawRunning.current = false; return; }
    if (operationState === "RUNNING") sawRunning.current = true;
    else if (sawRunning.current) setQueuedNotice(false);
  }, [queuedNotice, operationState, researchReadDenied]);
  const submit = useMutation({
    mutationFn: async (captured: { projectId: string; threadId: string; problem: string; selection: ModelSelection | null; origin: PendingOrigin | null; responseLanguage?: ResponseLanguage; revision: number; epoch: number; key: string; scope: BrowserScope }) => {
      const admission = await rpc<ResearchAdmission>(captured.threadId ? "thread/input" : "thread/start", {
        project_id: captured.projectId, contract_version: 2, ...captured.selection, ...(captured.responseLanguage ? { response_language: captured.responseLanguage } : {}), ...(captured.origin ? { origin: captured.origin } : {}),
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
        throw new UiError("submission.unconfirmed");
      }
      return value;
    },
    onSuccess: async (admission, captured) => {
      // Parent invalidates this token synchronously on project selection; late admissions cannot replace another draft.
      if (captured.epoch !== epoch.current) return;
      pendingSubmission.current = null;
      if (hasSubmissionStore(captured.scope)) {
        const removed = clearSubmission(captured.scope, captured.projectId, captured.threadId);
        if (removed) { setPendingRead({ kind: "NONE" }); setPendingStorageError(null); }
        else setPendingStorageError("submission.clearFailed");
      }
      clearModel(captured.revision, captured.epoch);
      if (captured.origin) onOriginDone?.();
      setQueuedNotice(admission.status === "QUEUED_AFTER_CURRENT");
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
  // "이어서 조사": the user asked to continue the interrupted run. The server reuses only the stages whose input is unchanged.
  const resume = useMutation({
    mutationFn: async (operationId: string) => {
      if (!canExecute || !threadId) throw new UiError("submission.resumeUnavailable");
      return rpc<ResearchAdmission>("thread/input", { project_id: projectId, thread_id: threadId, contract_version: 2, resume_from_operation_id: operationId }, crypto.randomUUID());
    },
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["threads"] }),
        client.invalidateQueries({ queryKey: ["research", projectId, threadId] }),
        client.invalidateQueries({ queryKey: ["conversation", projectId, threadId] }),
      ]);
    },
  });
  const retryPending = () => {
    if (!isCurrentScope() || !hasSubmissionStore(storageScope) || !canExecute || pendingSubmission.current?.inFlight) return;
    const stored = readSubmission(storageScope, projectId, threadId);
    setPendingRead(stored);
    if (stored.kind !== "PENDING") return;
    const { value } = stored;
    if (value.responseLanguage && !languageSupported) return;
    pendingSubmission.current = { signature: value.signature, key: value.key, inFlight: true };
    setPendingStorageError(null);
    submit.mutate({ projectId, threadId, problem: value.problem, selection: value.selection, origin: value.origin ?? null, responseLanguage: value.responseLanguage,
      revision: -1, epoch: scopeEpoch, key: value.key, scope: storageScope });
  };
  return { research, researchReadDenied, queuedNotice, problem, setProblem, draftSaveState, draftRestoreIssue, pendingRead, pendingBlocksNewInput, pendingStorageError,
    retryPending, modelSelection, chooseModel, clearModel, draftRevision, responseLanguage, languageSupported,
    renderedRevision: draftRevision.current, submit, resume, isCurrentScope,
    send: () => {
      if (!isCurrentScope() || !canExecute || responseLanguage !== null && !languageSupported || !problem.trim() || pendingSubmission.current?.inFlight || pendingBlocksNewInput) return;
      if (hasSubmissionStore(storageScope)) {
        const stored = readSubmission(storageScope, projectId, threadId);
        setPendingRead(stored);
        if (stored.kind === "INVALID" || stored.kind === "PENDING" && stored.value.signature !== currentSignature) return;
        const record: PendingSubmission = stored.kind === "PENDING" ? stored.value : {
          ...(responseLanguage ? { version: 2 as const, responseLanguage } : { version: 1 as const }), workspaceId: submissionScopeId(storageScope) ?? "", projectId, threadId,
          problem, selection: modelSelection, ...(origin ? { origin } : {}), signature: currentSignature, key: crypto.randomUUID(), createdAt: Date.now(),
        };
        if (stored.kind === "NONE") {
          const saved = writeSubmission(storageScope, record);
          if (saved !== "SAVED") {
            setPendingStorageError(saved === "TOO_LARGE" ? "submission.tooLarge"
              : "submission.storageFailed");
            return;
          }
          setPendingRead({ kind: "PENDING", value: record });
        }
        pendingSubmission.current = { signature: record.signature, key: record.key, inFlight: true };
        setPendingStorageError(null);
        submit.mutate({ projectId, threadId, problem: record.problem, selection: record.selection, origin: record.origin ?? null, responseLanguage: record.responseLanguage,
          revision: draftRevision.current, epoch: scopeEpoch, key: record.key, scope: storageScope });
        return;
      }
      const signature = JSON.stringify([currentSignature, scopeEpoch]);
      if (pendingSubmission.current?.signature !== signature) pendingSubmission.current = {signature,key:crypto.randomUUID(),inFlight:false};
      const request = pendingSubmission.current;
      request.inFlight = true;
      submit.mutate({ projectId, threadId, problem, selection: modelSelection, origin, responseLanguage: responseLanguage ?? undefined, revision: draftRevision.current, epoch: scopeEpoch, key:request.key, scope: storageScope });
    },
  };
}
