import { message as formatMessage } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { Button, Callout, Classes, Dialog, Radio, RadioGroup } from "@blueprintjs/core";
import { useState } from "react";
import { applyTraceImport, previewTraceImport, type ImportApplied, type ImportMode, type ImportPreview } from "../api/trace";
import { applyFailureText, issueText, lossText } from "./traceText";

const kindName: Record<string, string> = { get ITEM() { return t("traceImport.item"); }, get LINK() { return t("traceImport.link"); }, get RULE() { return t("traceImport.rule"); }, get RESULT() { return t("traceImport.result"); } };

function readText(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

function ChangeList({ heading, items }: { heading: string; items: { kind: string; id: string; fields: string[] }[] }) {
  if (items.length === 0) return null;
  return <div><strong>{formatMessage("itemCount", heading, items.length)}</strong>
    <ul className="trace-reasons">{items.map(item => <li key={item.kind + item.id}>{kindName[item.kind] ?? item.kind} {item.id}</li>)}</ul></div>;
}

/** Choose a file, look at what it would change, then apply exactly that. Nothing is saved before the last button. */
export function TraceImportDialog({ projectId, isOpen, hasTrace, onClose, onApplied }: {
  projectId: string; isOpen: boolean; hasTrace: boolean; onClose: () => void; onApplied: (result: ImportApplied) => void;
}) {
  const [mode, setMode] = useState<ImportMode>(hasTrace ? "UPDATE" : "CREATE");
  const [text, setText] = useState<string | null>(null);
  const [fileName, setFileName] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ message: string; next: string } | null>(null);
  const reset = () => { setText(null); setFileName(""); setPreview(null); setFailure(null); setBusy(false); };
  const close = () => { reset(); onClose(); };
  const look = async (nextMode: ImportMode, csv: string | null) => {
    setPreview(null); setFailure(null);
    if (csv === null) return;
    setBusy(true);
    try { setPreview(await previewTraceImport(projectId, nextMode, csv)); }
    catch { setFailure({ get message() { return t("traceImport.failed"); }, get next() { return t("trace.apply.retry"); } }); }
    finally { setBusy(false); }
  };
  const choose = async (file: File | undefined) => {
    if (!file) return;
    const csv = await readText(file);
    setText(csv); setFileName(file.name);
    await look(mode, csv);
  };
  const apply = async () => {
    if (!preview || text === null) return;
    setBusy(true); setFailure(null);
    try { const result = await applyTraceImport(projectId, mode, text, preview); reset(); onApplied(result); }
    catch (error) { setFailure(applyFailureText(error)); setBusy(false); }
  };
  const changes = preview?.changes;
  const total = changes ? changes.added.length + changes.updated.length + changes.deleted.length : 0;
  return <Dialog isOpen={isOpen} onClose={close} title={t("tracePage.import")} className="trace-import-dialog">
    <div className={Classes.DIALOG_BODY}>
      <RadioGroup label={t("traceImport.mode")} selectedValue={mode} onChange={event => { const next = event.currentTarget.value as ImportMode; setMode(next); void look(next, text); }}>
        <Radio value="CREATE" label={t("traceImport.create")}/>
        <Radio value="UPDATE" label={t("traceImport.update")}/>
      </RadioGroup>
      <label className="trace-file-field">{t("traceImport.chooseFile")}<input type="file" accept=".csv,text/csv" aria-label={t("traceImport.chooseFile")} onChange={event => void choose(event.currentTarget.files?.[0])}/></label>
      {fileName && <p className="muted">{t("labels.selectedFile")}{" "}{fileName}</p>}
      {busy && <p role="status">{t("traceImport.working")}</p>}
      {failure && <Callout intent="danger" role="alert">{failure.message}<p><strong>{t("delta.nextAction")}</strong>: {failure.next}</p></Callout>}
      {preview && changes && <section aria-label={t("traceImport.preview")} className="trace-preview">
        <h3>{t("traceImport.notSaved")}</h3>
        <p>{formatMessage("importCounts", changes.added.length, changes.updated.length, changes.deleted.length, changes.unchanged)}</p>
        <ChangeList heading={t("traceImport.added")} items={changes.added}/><ChangeList heading={t("history.change")} items={changes.updated}/><ChangeList heading={t("traceImport.deleted")} items={changes.deleted}/>
        {preview.conflicts.length > 0 && <Callout intent="danger" title={t("traceImport.issues")} role="alert">
          <ul className="trace-reasons">{preview.conflicts.map((issue, index) => { const info = issueText(issue);
            return <li key={index}>{info.message}<br/><strong>{t("delta.nextAction")}</strong>: {info.next}
              <details className="connection-tech"><summary>{t("oauth.technical")}</summary><small>{issue.code}{issue.detail ? ` · ${issue.detail}` : ""}</small></details></li>; })}</ul></Callout>}
        {preview.losses.length > 0 && <details className="connection-tech"><summary>{t("traceImport.untouched")}</summary>
          <ul className="trace-reasons">{preview.losses.map(line => <li key={line}>{lossText(line)}</li>)}</ul></details>}
        {preview.applicable && total === 0 && <p>{t("traceImport.noChange")}</p>}
        {preview.applicable && total > 0 && <p className="muted">{t("traceImport.recompute")}</p>}
      </section>}
    </div>
    <div className={Classes.DIALOG_FOOTER}><div className={Classes.DIALOG_FOOTER_ACTIONS}>
      <Button onClick={close}>{t("hold.close")}</Button>
      <Button intent="primary" disabled={!preview || !preview.applicable || total === 0 || busy} onClick={() => void apply()}>{t("traceImport.apply")}</Button>
    </div></div>
  </Dialog>;
}
