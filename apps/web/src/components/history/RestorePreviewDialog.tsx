import { Button, Callout, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";
import { useEffect, useRef } from "react";
import type { HistoryScope, RestoreResult, RestoreSelection } from "../../api/historyModels";
import { RevisionComparison } from "./RevisionComparison";
import { useRestoreFlow } from "./useRestoreFlow";
import { restoreErrorText } from "./historyErrors";
import { Disclosure } from "../Disclosure";

export function RestorePreviewDialog({ scope, selection, actorScope, title, onClose, onApplied, onRefresh }: {
  scope: HistoryScope; selection: RestoreSelection; actorScope: string; title: string;
  onClose: () => void; onApplied: (result: RestoreResult) => Promise<void>; onRefresh: () => Promise<void>;
}) {
  const flow = useRestoreFlow(scope, selection, actorScope, onApplied);
  const completion = useRef<HTMLDivElement | null>(null);
  useEffect(() => { if (flow.readback) completion.current?.focus(); }, [flow.readback]);
  const preview = flow.preview.data;
  const canApply = Boolean(preview?.applyReady && preview.availability === "AVAILABLE" && preview.basisDigest
    && preview.principalScope === actorScope && !flow.pending && !flow.confirmed && !flow.busy
    && !flow.error && !flow.preview.error && !flow.preview.isFetching);
  const status = flow.readback ? flow.confirmed?.disposition === "NO_CHANGE" ? "현재 내용과 같아 새 버전을 만들지 않았습니다." : "내용을 복원했습니다. 관련 항목은 재검토가 필요합니다."
    : flow.confirmed ? "적용 결과를 받았습니다. 현재 기록을 다시 확인해야 합니다."
    : flow.pending ? "원 요청의 결과를 확인해야 합니다. 실행 종료 여부는 아직 확인되지 않았습니다." : null;
  return <Dialog isOpen className="bp6-dark history-restore-dialog" title="복원 내용 확인" onClose={onClose}
    canEscapeKeyClose={!flow.busy} canOutsideClickClose={!flow.busy} isCloseButtonShown={!flow.busy}>
    <DialogBody>
      <h3>{title}</h3><p>선택한 과거 내용을 새 버전으로 적용합니다. 과거 기록과 이미 실행된 결과는 남습니다.</p>
      <p className="history-help">연구 재분석이나 모델 호출은 자동으로 시작되지 않습니다.</p>
      {flow.preview.isPending && <p role="status">대상과 영향을 확인하는 중…</p>}
      {flow.preview.error && !flow.pending && <Callout intent="warning" role="alert">미리보기를 읽지 못했습니다. 기록을 새로 읽고 다시 확인하세요.</Callout>}
      {preview && <>
        {preview.principalScope !== actorScope && <Callout compact intent="warning">접근 범위가 바뀌었습니다. 대화상자를 닫고 이력을 새로 읽어 주세요.</Callout>}
        {preview.availability === "NO_CHANGE" && <Callout compact>현재 내용과 같습니다. 복원할 변경이 없습니다.</Callout>}
        {(!preview.applyReady || preview.availability === "BLOCKED") && <Callout compact intent="warning">현재 이 항목은 복원을 적용할 수 없습니다. 이력과 변경 내용은 확인할 수 있습니다.</Callout>}
        <RevisionComparison comparison={preview.comparison} beforeLabel="현재 내용" afterLabel="적용할 내용" title="적용할 변경" />
        <section className="restore-impact"><h4>연결된 항목에 미치는 영향</h4>
          {preview.impacts.length ? <><p>{preview.impacts.length}개 연결 항목의 현재 사용 상태를 다시 확인해야 합니다.</p>
            <Disclosure label="영향받는 기록 확인"><ul>{preview.impacts.map(item => <li key={item.label}>{item.state} <code>{item.label}</code></li>)}</ul></Disclosure></>
            : <p>이 미리보기에 표시된 추가 영향 항목은 없습니다.</p>}
        </section>
        {preview.sourceChanges.length > 0 && <Callout compact intent="warning">자료의 버전이나 접근 상태가 바뀌었습니다. 현재 미리보기로 적용할 수 있는지 다시 확인해야 합니다.</Callout>}
        {preview.reasons.length > 0 && <Disclosure className="history-technical" label="적용 조건 확인"><ul>{preview.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></Disclosure>}
      </>}
      {status && <div ref={completion} tabIndex={-1} role="status"><Callout intent={flow.readback ? "success" : "warning"}>{status}</Callout></div>}
      {flow.error && <Callout intent="warning" role="alert">{restoreErrorText(flow.error)}<Disclosure defaultOpen label="요청 진단"><pre>{flow.error.message}</pre></Disclosure></Callout>}
    </DialogBody>
    <DialogFooter actions={<>
      <Button onClick={onClose} disabled={flow.busy}>{flow.readback ? "닫기" : "돌아가기"}</Button>
      {!flow.pending && !flow.confirmed && <Button disabled={flow.busy} onClick={() => void onRefresh()}>기록 새로 읽기</Button>}
      {(flow.pending || (flow.confirmed && !flow.readback)) ? <Button intent="primary" loading={flow.busy} onClick={() => void flow.run("check")}>원 요청 결과 확인</Button>
        : !flow.readback && <Button intent="primary" disabled={!canApply} loading={flow.busy} onClick={() => void flow.run("apply")}>선택한 내용 적용</Button>}
    </>} />
  </Dialog>;
}
