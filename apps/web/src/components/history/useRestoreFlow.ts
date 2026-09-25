import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RpcError } from "../../api/rpcClient";
import { captureRestoreAttempt, readRestoreOperation, readRestorePreview, sendRestoreAttempt, type RestoreOutcome } from "../../api/restore";
import { clearPendingRestore, loadPendingRestore, savePendingRestore, type PendingRestore } from "../../api/restorePending";
import { restoreKey, type HistoryScope, type RestoreResult, type RestoreSelection } from "../../api/historyModels";

export function useRestoreFlow(scope: HistoryScope, selection: RestoreSelection, actorScope: string, onApplied: (result: RestoreResult) => Promise<void>) {
  const active = useRef(true);
  const sending = useRef(false);
  const [pending, setPending] = useState<PendingRestore | null>(() => loadPendingRestore(scope, selection, actorScope));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [confirmed, setConfirmed] = useState<RestoreResult | null>(null);
  const [readback, setReadback] = useState(false);
  const preview = useQuery({ queryKey: ["restore-preview", scope.projectId, restoreKey(selection), actorScope],
    queryFn: ({ signal }) => readRestorePreview(selection, signal), retry: false, refetchOnWindowFocus: false });
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const remember = (next: PendingRestore) => {
    savePendingRestore(scope, selection, next.attempt, next.operationId);
    if (active.current) setPending(next);
  };
  const confirmReadback = async (result: RestoreResult) => {
    if (!active.current) return;
    setConfirmed(result);
    await onApplied(result);
    if (!active.current) return;
    clearPendingRestore(scope, selection, actorScope);
    setPending(null);
    setReadback(true);
  };
  const accept = async (outcome: RestoreOutcome, current: PendingRestore) => {
    if (outcome.state === "RUNNING") remember({ ...current, operationId: outcome.operationId });
    else {
      remember({ ...current, operationId: outcome.result.operationId });
      await confirmReadback(outcome.result);
    }
  };
  const run = async (mode: "apply" | "check") => {
    if (sending.current || !active.current) return;
    if (pending && pending.attempt.principalScope !== actorScope) {
      setError(new Error("다른 로그인 또는 접근 범위의 요청은 다시 보내지 않습니다. 기록을 새로 읽어 주세요.")); return;
    }
    const currentPreview = preview.data;
    if (currentPreview && currentPreview.principalScope !== actorScope) {
      setError(new Error("로그인 또는 접근 범위가 바뀌었습니다. 기록을 새로 읽어 주세요.")); return;
    }
    if (mode === "apply" && (!currentPreview || pending || confirmed || error || preview.isFetching || preview.error)) return;
    if (mode === "check" && !pending && !confirmed) return;
    // Existing attempts retain their original selection, basis and key after a head change.
    let attempt = pending;
    try {
      if (!attempt && currentPreview && !confirmed) attempt = { version: 1, attempt: captureRestoreAttempt(currentPreview), operationId: null };
    } catch (cause) { setError(cause instanceof Error ? cause : new Error("복원을 준비하지 못했습니다.")); return; }
    sending.current = true; setBusy(true); setError(null);
    try {
      if (confirmed) await confirmReadback(confirmed);
      else if (attempt) {
        remember(attempt);
        const outcome = mode === "check" && attempt.operationId
          ? await readRestoreOperation(attempt.attempt, attempt.operationId)
          : await sendRestoreAttempt(attempt.attempt);
        await accept(outcome, attempt);
      }
    } catch (cause) {
      if (!active.current) return;
      setError(cause instanceof Error ? cause : new Error("요청 결과를 확인하지 못했습니다."));
      // A definitive server refusal permits a new preview; transport/schema failures stay pending.
      if (cause instanceof RpcError && !confirmed && cause.details.original_operation_unchanged !== true
        && /^RESTORE_(HEAD_CHANGED|PREVIEW_STALE|SOURCE_UNAVAILABLE|SOURCE_DRIFT|SCHEMA_UNSUPPORTED|APPLY_NOT_READY|NOT_READY)$/.test(String(cause.details.reason_code))) {
        clearPendingRestore(scope, selection, actorScope); setPending(null);
      }
    } finally { sending.current = false; if (active.current) setBusy(false); }
  };
  return { preview, pending, busy, error, confirmed, readback, run };
}
