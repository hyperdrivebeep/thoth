import { message as formatMessage } from "../../i18n/formatMessages";
import { t } from "../../i18n/locale";
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
  const status = flow.readback ? flow.confirmed?.disposition === "NO_CHANGE" ? t("restore.noChange") : t("restore.applied")
    : flow.confirmed ? t("restore.readback")
    : flow.pending ? t("restore.pending") : null;
  return <Dialog isOpen className="bp6-dark history-restore-dialog" title={t("restore.title")} onClose={onClose}
    canEscapeKeyClose={!flow.busy} canOutsideClickClose={!flow.busy} isCloseButtonShown={!flow.busy}>
    <DialogBody>
      <h3>{title}</h3><p>{t("restore.notice")}</p>
      <p className="history-help">{t("restore.noModel")}</p>
      {flow.preview.isPending && <p role="status">{t("restore.loading")}</p>}
      {flow.preview.error && !flow.pending && <Callout intent="warning" role="alert">{t("restore.previewFailed")}</Callout>}
      {preview && <>
        {preview.principalScope !== actorScope && <Callout compact intent="warning">{t("restore.scopeChanged")}</Callout>}
        {preview.availability === "NO_CHANGE" && <Callout compact>{t("restore.same")}</Callout>}
        {(!preview.applyReady || preview.availability === "BLOCKED") && <Callout compact intent="warning">{t("restore.unavailable")}</Callout>}
        <RevisionComparison comparison={preview.comparison} beforeLabel={t("restore.current")} afterLabel={t("restore.proposed")} title={t("restore.changes")} />
        <section className="restore-impact"><h4>{t("restore.impact")}</h4>
          {preview.impacts.length ? <><p>{formatMessage("restoreImpacts", preview.impacts.length)}</p>
            <Disclosure label={t("restore.impactedRecords")}><ul>{preview.impacts.map(item => <li key={item.label}>{item.state} <code>{item.label}</code></li>)}</ul></Disclosure></>
            : <p>{t("restore.noExtraImpact")}</p>}
        </section>
        {preview.sourceChanges.length > 0 && <Callout compact intent="warning">{t("restore.sourceChanged")}</Callout>}
        {preview.reasons.length > 0 && <Disclosure className="history-technical" label={t("restore.conditions")}><ul>{preview.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></Disclosure>}
      </>}
      {status && <div ref={completion} tabIndex={-1} role="status"><Callout intent={flow.readback ? "success" : "warning"}>{status}</Callout></div>}
      {flow.error && <Callout intent="warning" role="alert">{restoreErrorText(flow.error)}<Disclosure defaultOpen label={t("restore.requestDiagnostics")}><pre>{flow.error.message}</pre></Disclosure></Callout>}
    </DialogBody>
    <DialogFooter actions={<>
      <Button onClick={onClose} disabled={flow.busy}>{flow.readback ? t("hold.close") : t("restore.back")}</Button>
      {!flow.pending && !flow.confirmed && <Button disabled={flow.busy} onClick={() => void onRefresh()}>{t("restore.reload")}</Button>}
      {(flow.pending || (flow.confirmed && !flow.readback)) ? <Button intent="primary" loading={flow.busy} onClick={() => void flow.run("check")}>{t("restore.checkOriginal")}</Button>
        : !flow.readback && <Button intent="primary" disabled={!canApply} loading={flow.busy} onClick={() => void flow.run("apply")}>{t("restore.apply")}</Button>}
    </>} />
  </Dialog>;
}
